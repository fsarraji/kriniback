from django.http import HttpResponse
from django.template.loader import get_template
from rest_framework import viewsets, permissions, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from django.core.files.base import ContentFile
from django.shortcuts import get_object_or_404
from django.core.cache import cache
from functools import lru_cache
import logging
import os
import base64
import requests
import threading
import traceback
from time import perf_counter
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont
import math
from io import BytesIO
import qrcode
import barcode
from barcode.writer import ImageWriter
from django_filters.rest_framework import DjangoFilterBackend
from .models import Contract, ContractDamage, PdfJob, BookingRequest, Reservation, _release_vehicle
from .serializers import ContractSerializer, PdfJobSerializer, BookingRequestSerializer, ReservationSerializer

logger = logging.getLogger(__name__)

_pdf_session = requests.Session()


def generate_pdf(html_string):
    service_url = settings.PDF_SERVICE_URL.rstrip('/') + '/convert'
    html_size = len(html_string.encode('utf-8'))

    started = perf_counter()
    try:
        resp = _pdf_session.post(service_url, json={"html": html_string}, timeout=(10, 90))
    except requests.RequestException as e:
        logger.error("pdf_service_http_error", extra={
            "error": str(e),
            "html_size": html_size,
        })
        raise
    http_time = perf_counter() - started

    resp.raise_for_status()

    pdf = resp.content

    if not pdf:
        raise RuntimeError("PDF service returned an empty response")

    if not pdf.startswith(b"%PDF"):
        raise RuntimeError("PDF service returned an invalid PDF")

    logger.info("pdf_generated", extra={
        "html_size": html_size,
        "pdf_size": len(pdf),
        "http_time": round(http_time, 3),
    })

    return pdf

def generate_fuel_gauge_image(level_str):
    if not level_str:
        return None
    try:
        level = int(level_str.split('/')[0])
    except:
        level = 0
    levels = 8
    
    # Smaller canvas (150x90) to save memory
    width, height = 150, 90
    # Use white background (no alpha) to allow JPEG conversion
    img = Image.new('RGB', (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    
    cx, cy = 75, 75
    r = 60
    
    # Track background
    draw.arc([cx - r, cy - r, cx + r, cy + r], 180, 0, fill='#e2e8f0', width=3)
    
    try:
        font = ImageFont.truetype("arial.ttf", size=14)
        font_small = ImageFont.truetype("arial.ttf", size=10)
    except IOError:
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()
        
    draw.text((20, 80), "E", fill='#0f172a', font=font, anchor="mm")
    draw.text((130, 80), "F", fill='#0f172a', font=font, anchor="mm")
    
    for i in range(levels + 1):
        a = 180 - (i / levels) * 180
        rad = math.radians(a)
        is_major = i % 2 == 0
        tick_len = 10 if is_major else 5
        r_in = r - tick_len
        r_out = r
        x1 = cx + r_in * math.cos(rad)
        y1 = cy - r_in * math.sin(rad)
        x2 = cx + r_out * math.cos(rad)
        y2 = cy - r_out * math.sin(rad)
        
        draw.line((x1, y1, x2, y2), fill='#94a3b8', width=2 if is_major else 1)
        
        if is_major:
            # Shift text towards center
            tx = cx + (r - 20) * math.cos(rad)
            ty = cy - (r - 20) * math.sin(rad)
            draw.text((tx, ty), str(i), fill='#64748b', font=font_small, anchor="mm")
            
    level_val = min(max(level, 0), levels)
    angle = 180 - (level_val / levels) * 180
    rad = math.radians(angle)
    pointer_len = r - 5
    px = cx + pointer_len * math.cos(rad)
    py = cy - pointer_len * math.sin(rad)
    
    # Drawing needle
    draw.line((cx, cy, px, py), fill='#ef4444', width=3)
    # Center dot
    draw.ellipse((cx-5, cy-5, cx+5, cy+5), fill='#1e293b')
    
    buffered = BytesIO()
    # JPEG is extremely compact for such simple graphics
    img.save(buffered, format="JPEG", quality=70, optimize=True)
    img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
    return f"data:image/jpeg;base64,{img_str}"


@lru_cache(maxsize=1)
def _get_car_diagram_source():
    car_diagram_path = os.path.join(settings.BASE_DIR, 'car_damage_diagram.png')
    if not os.path.exists(car_diagram_path):
        return None
    img = Image.open(car_diagram_path).convert('RGB')
    img.thumbnail((250, 250), Image.Resampling.LANCZOS)
    return img


@lru_cache(maxsize=128)
def _damage_diagram_base64(damage_signature, dot_color):
    src = _get_car_diagram_source()
    if src is None:
        return ""
    try:
        img = src.copy()
        draw = ImageDraw.Draw(img)
        w, h = img.size
        try:
            font = ImageFont.truetype("arial.ttf", size=int(w * 0.08))
        except IOError:
            font = ImageFont.load_default()
        for i, (x, y) in enumerate(damage_signature):
            x_px = (x / 100.0) * w
            y_px = (y / 100.0) * h
            radius = w * 0.04
            draw.ellipse((x_px - radius, y_px - radius, x_px + radius, y_px + radius), fill=dot_color)
            draw.text((x_px, y_px), str(i + 1), fill='white', font=font, anchor="mm")
        buffered = BytesIO()
        img.save(buffered, format="JPEG", quality=75, optimize=True)
        encoded = base64.b64encode(buffered.getvalue()).decode('utf-8')
        return f"data:image/jpeg;base64,{encoded}"
    except Exception:
        return ""


def _get_diagram_base64(damages, dot_color):
    signature = tuple((float(d.x), float(d.y)) for d in damages)
    return _damage_diagram_base64(signature, dot_color)


def _normalize_wa_phone(telephone):
    if not telephone:
        return None
    phone = telephone.replace(' ', '').replace('-', '').replace('.', '')
    if phone.startswith('0'):
        phone = '212' + phone[1:]
    elif phone.startswith('+'):
        phone = phone[1:]
    return phone or None


def _generate_whatsapp_qr_base64(wa_link):
    qr = qrcode.QRCode(version=1, box_size=10, border=1)
    qr.add_data(wa_link)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="#111827", back_color="white")
    qr_buf = BytesIO()
    qr_img.save(qr_buf, format="PNG")
    qr_b64 = base64.b64encode(qr_buf.getvalue()).decode('utf-8')
    return f"data:image/png;base64,{qr_b64}"


def get_whatsapp_qr_base64(agency):
    phone = _normalize_wa_phone(agency.telephone)
    if not phone:
        return ""
    cache_key = f"kricar:pdf:qr:{agency.id}:{phone}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    wa_link = f"https://wa.me/{phone}"
    qr_b64 = _generate_whatsapp_qr_base64(wa_link)
    cache.set(cache_key, qr_b64, 60 * 60 * 24 * 30)
    return qr_b64


def _optimize_stamp(image_field):
    img = Image.open(image_field).convert('RGBA')
    img.thumbnail((300, 150), Image.Resampling.LANCZOS)
    buffered = BytesIO()
    img.save(buffered, format="PNG", optimize=True)
    encoded = base64.b64encode(buffered.getvalue()).decode('utf-8')
    return f"data:image/png;base64,{encoded}"


def get_agency_stamp_base64(agency):
    if not agency.cachet_signature:
        return ""
    stamp_name = agency.cachet_signature.name
    cache_key = f"kricar:pdf:stamp:{agency.id}:{stamp_name}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    b64 = _optimize_stamp(agency.cachet_signature)
    cache.set(cache_key, b64, 60 * 60 * 24)
    return b64


def _contract_barcode_base64(contract):
    try:
        code_str = str(contract.id).zfill(4)
        COD128 = barcode.get_barcode_class('code128')
        bar = COD128(code_str, writer=ImageWriter())
        bar_buf = BytesIO()
        bar.write(bar_buf, options={"write_text": False, "module_height": 5.0, "module_width": 0.25})
        bar_b64 = base64.b64encode(bar_buf.getvalue()).decode('utf-8')
        return f"data:image/png;base64,{bar_b64}"
    except Exception:
        return ""


def build_contract_pdf(contract, agency, with_cachet=False):
    template_path = 'contracts/contract_pdf.html'
    start = perf_counter()

    depart_damages = contract.damages.filter(type='DEPART')
    retour_damages = contract.damages.filter(type='RETOUR')

    assets_start = perf_counter()

    car_diagram_base64 = _get_diagram_base64(depart_damages, 'red')
    retour_diagram_base64 = _get_diagram_base64(retour_damages, 'orange')

    fuel_depart_base64 = generate_fuel_gauge_image(contract.carburant_sortie)
    fuel_retour_base64 = generate_fuel_gauge_image(contract.carburant_retour)

    try:
        whatsapp_qr_base64 = get_whatsapp_qr_base64(agency)
    except Exception:
        whatsapp_qr_base64 = ""

    contract_barcode_base64 = _contract_barcode_base64(contract)

    cachet_base64 = ""
    if with_cachet and agency.cachet_signature:
        try:
            cachet_base64 = get_agency_stamp_base64(agency)
        except Exception as e:
            cachet_base64 = ""
            logger.warning("cachet_generation_error", extra={"agency_id": agency.id, "error": str(e)})

    assets_time = perf_counter() - assets_start

    # Calculating Finances
    km_parcourus = contract.km_retour - contract.km_sortie if contract.km_retour else None
    agency_km_extra_active = agency.km_extra_active
    agency_km_par_jour = agency.km_par_jour
    km_tarif_extra = float(contract.vehicle.tarif_km_extra or agency.km_tarif_extra_defaut or 1.5)
    km_inclus_total = (agency_km_par_jour * contract.jours) if agency_km_extra_active else None
    km_supplementaires = None
    montant_km_extra = None
    if agency_km_extra_active and km_parcourus is not None and km_inclus_total is not None:
        km_supplementaires = max(0, km_parcourus - km_inclus_total)
        montant_km_extra = round(km_supplementaires * km_tarif_extra, 2) if km_supplementaires > 0 else 0

    # Building Context
    context = {
        'contract': contract,
        'agency': agency,
        'car_diagram_base64': car_diagram_base64,
        'retour_diagram_base64': retour_diagram_base64,
        'depart_damages': depart_damages,
        'retour_damages': retour_damages,
        'fuel_depart_base64': fuel_depart_base64,
        'fuel_retour_base64': fuel_retour_base64,
        'whatsapp_qr_base64': whatsapp_qr_base64,
        'contract_barcode_base64': contract_barcode_base64,
        'km_parcourus': km_parcourus,
        'agency_km_extra_active': agency_km_extra_active,
        'agency_km_par_jour': agency_km_par_jour,
        'km_tarif_extra': km_tarif_extra,
        'km_inclus_total': km_inclus_total,
        'km_supplementaires': km_supplementaires,
        'montant_km_extra': montant_km_extra,
        'cachet_base64': cachet_base64,
    }

    template = get_template(template_path)
    html_start = perf_counter()
    html = template.render(context)
    template_time = perf_counter() - html_start

    pdf = generate_pdf(html)

    total_time = perf_counter() - start

    logger.info("contract_pdf_completed", extra={
        "contract_id": contract.id,
        "assets_time": round(assets_time, 3),
        "template_time": round(template_time, 3),
        "total_time": round(total_time, 3),
        "html_size": len(html.encode('utf-8')),
        "pdf_size": len(pdf),
    })

    return pdf


def build_receipt_pdf(contract, agency):
    template_path = 'contracts/reservation_receipt_pdf.html'
    start = perf_counter()

    try:
        whatsapp_qr_base64 = get_whatsapp_qr_base64(agency)
    except Exception:
        whatsapp_qr_base64 = ""

    context = {
        'contract': contract,
        'agency': agency,
        'whatsapp_qr_base64': whatsapp_qr_base64,
    }

    template = get_template(template_path)
    html = template.render(context)
    pdf = generate_pdf(html)

    total_time = perf_counter() - start

    logger.info("receipt_pdf_completed", extra={
        "contract_id": contract.id,
        "total_time": round(total_time, 3),
        "html_size": len(html.encode('utf-8')),
        "pdf_size": len(pdf),
    })

    return pdf


def process_pdf_job(job_id):
    try:
        job = PdfJob.objects.get(id=job_id)
        job.status = 'PROCESSING'
        job.save(update_fields=['status', 'updated_at'])

        contract = job.contract
        agency = contract.agency

        if job.job_type == 'contract':
            pdf_bytes = build_contract_pdf(contract, agency, job.with_cachet)
            filename = f"contrat_{contract.id}.pdf"
        else:
            pdf_bytes = build_receipt_pdf(contract, agency)
            filename = f"recu_{contract.id}.pdf"

        job.pdf_file.save(filename, ContentFile(pdf_bytes), save=False)
        job.status = 'READY'
        job.error_message = None
        job.save()
    except Exception as e:
        traceback.print_exc()
        PdfJob.objects.filter(id=job_id).update(
            status='ERROR',
            error_message=str(e),
        )

class ContractViewSet(viewsets.ModelViewSet):
    serializer_class = ContractSerializer
    permission_classes = [permissions.IsAuthenticated]

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['statut', 'client', 'vehicle']
    search_fields = ['client__nom', 'client__prenom', 'client__telephone', 'vehicle__matricule', 'vehicle__marque__name', 'vehicle__modele__name']
    ordering_fields = ['date_creation', 'montant_total', 'date_sortie', 'date_retour_prevue']

    def get_queryset(self):
        base = Contract.objects.select_related(
            'client',
            'vehicle__marque',
            'vehicle__modele',
            'deuxieme_chauffeur',
            'agency',
            'created_by',
        ).prefetch_related('damages').order_by('-date_creation')
        if self.request.user.is_superuser:
            return base
        return base.filter(agency=self.request.user.agency)

    def perform_create(self, serializer):
        serializer.save(agency=self.request.user.agency, created_by=self.request.user)

    # مسار مخصص لإغلاق العقد وإرجاع السيارة
    @action(detail=True, methods=['post'])
    def return_vehicle(self, request, pk=None):
        contract = self.get_object()

        # التأكد من أن العقد لم ينتهِ بالفعل
        if contract.statut == 'TERMINE':
            return Response({'detail': 'هذا العقد منتهي بالفعل.'}, status=status.HTTP_400_BAD_REQUEST)
        if contract.statut == 'ANNULE':
            return Response({'detail': 'لا يمكن إغلاق عقد ملغى.'}, status=status.HTTP_400_BAD_REQUEST)

        # --- البيانات الأساسية ---
        km_retour = request.data.get('km_retour')
        carburant_retour = request.data.get('carburant_retour')
        degats_retour = request.data.get('degats_retour', '')
        date_retour_effective = request.data.get('date_retour_effective')

        if not km_retour:
            return Response({'detail': 'يجب إدخال الكيلومتراج عند الإرجاع.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            km_retour_int = int(km_retour)
        except (ValueError, TypeError):
            return Response({'detail': 'الكيلومتراج يجب أن يكون رقماً صحيحاً.'}, status=status.HTTP_400_BAD_REQUEST)

        if km_retour_int < contract.km_sortie:
            return Response(
                {'detail': f'كيلومتراج الإرجاع ({km_retour_int}) لا يمكن أن يكون أقل من كيلومتراج الخروج ({contract.km_sortie}).'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # --- تحديث الحقول الأساسية ---
        contract.km_retour = km_retour_int
        contract.carburant_retour = carburant_retour
        contract.degats_retour = degats_retour

        if date_retour_effective:
            from django.utils.dateparse import parse_datetime
            parsed_date = parse_datetime(date_retour_effective)
            contract.date_retour_effective = parsed_date if parsed_date else timezone.now()
        else:
            contract.date_retour_effective = timezone.now()

        # --- Recalcul de la durée et du montant ---
        import math
        diff = contract.date_retour_effective - contract.date_sortie
        # Calcul des jours (tout jour entamé est dû, min 1 jour)
        new_jours = math.ceil(diff.total_seconds() / (24 * 3600))
        if new_jours < 1:
            new_jours = 1
        
        contract.jours = new_jours
        # Le save() mettra à jour montant_total et reste_a_payer
        contract.save()

        # --- Traitement du paiement (Règlement final) ---
        payment_amount = request.data.get('payment_amount', 0)
        payment_method = request.data.get('payment_method', 'Espèce')
        
        if payment_amount and float(payment_amount) > 0:
            from payments.models import Payment
            Payment.objects.create(
                agency=contract.agency,
                contract=contract,
                user=request.user,
                amount=payment_amount,
                payment_method=payment_method,
                notes="Règlement final lors de la clôture du contrat"
            )
            # Recharger pour avoir les montants à jour (montant_paye/reste_a_payer)
            contract.refresh_from_db()

        # --- ÉTAT DES ACCESSOIRES AU RETOUR ---
        accessories_retour = request.data.get('accessories_retour', {})
        if isinstance(accessories_retour, dict):
            contract.roue_secours_retour = accessories_retour.get('roue_secours', False)
            contract.cric_retour = accessories_retour.get('cric', False)
            contract.manivelle_retour = accessories_retour.get('manivelle', False)
            contract.gilet_retour = accessories_retour.get('gilet', False)
            contract.triangle_retour = accessories_retour.get('triangle', False)
            contract.extincteur_retour = accessories_retour.get('extincteur', False)
            contract.papiers_retour = accessories_retour.get('papiers', False)
            contract.cles_retour = accessories_retour.get('cles', False)

        # --- نقاط الأضرار عند الإرجاع ---
        damages_retour = request.data.get('damages_retour', [])
        if isinstance(damages_retour, list):
            # حذف أي أضرار سابقة من نوع RETOUR لهذا العقد
            contract.damages.filter(type='RETOUR').delete()
            for dmg in damages_retour:
                try:
                    ContractDamage.objects.create(
                        contract=contract,
                        type='RETOUR',
                        x=float(dmg.get('x', 0)),
                        y=float(dmg.get('y', 0)),
                        description=dmg.get('description', '')
                    )
                except (ValueError, TypeError):
                    pass

        contract.statut = 'TERMINE'
        contract.save()

        # ✅ Remettre le véhicule en disponible (si aucune autre location en cours)
        vehicle = contract.vehicle
        vehicle.kilometrage = km_retour_int  # Mettre à jour le kilométrage du véhicule
        vehicle.save(update_fields=['kilometrage'])
        _release_vehicle(vehicle)

        return Response({
            'detail': 'تم إغلاق العقد وإرجاع السيارة بنجاح.',
            'reste_a_payer': str(contract.reste_a_payer),
            'date_retour_effective': contract.date_retour_effective.isoformat() if contract.date_retour_effective else None,
            'km_parcourus': contract.km_retour - contract.km_sortie,
        }, status=status.HTTP_200_OK)
        
    @action(detail=True, methods=['get'])
    def print_contract(self, request, pk=None):
        try:
            contract = self.get_object()
            agency = request.user.agency
            with_cachet = request.GET.get('with_cachet', 'false').lower() == 'true'

            pdf_file = build_contract_pdf(contract, agency, with_cachet)

            response = HttpResponse(pdf_file, content_type='application/pdf')
            response['Content-Disposition'] = f'inline; filename="Contrat_{contract.id}.pdf"'
            return response

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print("PDF GENERATION ERROR: ", error_details)
            return HttpResponse(f'عذراً، حدث خطأ أثناء توليد ملف الـ PDF.', status=500)


    @action(detail=True, methods=['get'])
    def print_reservation_receipt(self, request, pk=None):
        contract = self.get_object()
        agency = request.user.agency

        try:
            pdf_file = build_receipt_pdf(contract, agency)
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print("PDF GENERATION ERROR: ", error_details)
            return Response(f'عذراً، حدث خطأ أثناء توليد ملف الـ PDF: {str(e)}\nDetails: {error_details}', status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="Recu_Reservation_{contract.id}.pdf"'
        return response


class BookingRequestViewSet(viewsets.ModelViewSet):
    """
    Demandes de réservation des clients (leads).

    - La création (POST) est publique : les clients peuvent réserver sans compte.
    - La consultation et la gestion (GET/PATCH) sont réservées au personnel de l'agence.
    """
    serializer_class = BookingRequestSerializer

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['statut']
    search_fields = ['nom', 'prenom', 'telephone', 'email', 'vehicle__matricule']
    ordering_fields = ['created_at', 'statut']

    def get_permissions(self):
        if self.action == 'create':
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        if self.request.user.is_superuser:
            return BookingRequest.objects.all()
        return BookingRequest.objects.filter(agency=self.request.user.agency)

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        """
        Confirme une demande de réservation (BookingRequest) :
        1. Crée automatiquement le client s'il n'existe pas encore dans l'agence.
        2. Crée un contrat en statut RESERVE.
        3. Marque la demande comme CONFIRMED.
        """
        booking = self.get_object()

        if booking.statut != 'PENDING':
            return Response(
                {'detail': 'Seule une demande en attente peut être confirmée.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not booking.vehicle:
            return Response(
                {'detail': 'Aucun véhicule associé à cette demande.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        agency = booking.agency
        from clients.models import Client
        import math

        # 1. Chercher un client existant dans l'agence par téléphone
        client = Client.objects.filter(agency=agency, telephone=booking.telephone).first()

        # 2. Si introuvable, créer automatiquement la fiche client
        if not client:
            # Générer un cin_passport temporaire unique si non fourni
            temp_cin = f"TEMP-{booking.telephone.replace(' ', '')}"
            client = Client.objects.create(
                agency=agency,
                nom=booking.nom,
                prenom=booking.prenom,
                telephone=booking.telephone,
                email=booking.email or '',
                cin_passport=temp_cin,
                permis_conduite=temp_cin,  # temporaire, à compléter par l'agence
                adresse='',
                remarques=f"Créé automatiquement depuis la demande de réservation #{booking.id}",
            )

        # 3. Calcul de la durée
        diff = booking.date_retour_prevue - booking.date_sortie
        jours = max(1, math.ceil(diff.total_seconds() / (24 * 3600)))

        # 4. Créer le contrat en statut RESERVE
        from contracts.models import Contract
        contract = Contract.objects.create(
            agency=agency,
            created_by=request.user,
            vehicle=booking.vehicle,
            client=client,
            date_sortie=booking.date_sortie,
            date_retour_prevue=booking.date_retour_prevue,
            jours=jours,
            prix_par_jour=booking.vehicle.prix_par_jour,
            caution=agency.caution_montant if agency.caution_active else 0,
            km_sortie=booking.vehicle.kilometrage or 0,
            carburant_sortie='4/8',
            statut='RESERVE',
            notes=booking.message or '',
        )

        # 5. Marquer la demande comme CONFIRMÉE
        booking.statut = 'CONFIRMED'
        booking.save(update_fields=['statut'])

        return Response({
            'detail': 'Demande confirmée. Client et contrat créés avec succès.',
            'contract_id': contract.id,
            'client_id': client.id,
            'client_created': client.remarques and 'automatiquement' in client.remarques,
        }, status=status.HTTP_201_CREATED)


class ReservationViewSet(viewsets.ModelViewSet):
    """
    Réservations des clients.

    - Un client connecté peut créer sa réservation, consulter les siennes et annuler une réservation en attente.
    - Le personnel de l'agence voit les réservations de son agence et peut les confirmer (→ contrat RESERVE).
    """
    serializer_class = ReservationSerializer
    permission_classes = [permissions.IsAuthenticated]

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['statut']
    search_fields = ['client__nom', 'client__prenom', 'client__telephone', 'vehicle__matricule', 'vehicle__marque__name', 'vehicle__modele__name']
    ordering_fields = ['created_at', 'date_sortie', 'prix_par_jour']

    def get_queryset(self):
        if self.request.user.role == 'CLIENT':
            client = getattr(self.request.user, 'client_profile', None)
            if not client:
                return Reservation.objects.none()
            return Reservation.objects.filter(client=client)
        if self.request.user.is_superuser:
            return Reservation.objects.all()
        return Reservation.objects.filter(agency=self.request.user.agency)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    def partial_update(self, request, *args, **kwargs):
        reservation = self.get_object()
        # Un client ne peut que annuler sa propre réservation en attente
        if request.user.role == 'CLIENT':
            if reservation.client != getattr(request.user, 'client_profile', None):
                return Response({'detail': 'Réservation introuvable.'}, status=status.HTTP_404_NOT_FOUND)
            if reservation.statut != 'PENDING' or request.data.get('statut') != 'CANCELLED':
                return Response({'detail': 'Vous ne pouvez qu\'annuler une réservation en attente.'}, status=status.HTTP_403_FORBIDDEN)
        else:
            # Rétrocompatibilité : PATCH statut=CONFIRMED sur une réservation en attente
            # crée aussi le contrat (anciennes versions de l'app mobile).
            if request.data.get('statut') == 'CONFIRMED' and reservation.statut == 'PENDING':
                return self.confirm(request)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        reservation = self.get_object()
        if request.user.role == 'CLIENT':
            if reservation.client != getattr(request.user, 'client_profile', None):
                return Response({'detail': 'Réservation introuvable.'}, status=status.HTTP_404_NOT_FOUND)
            if reservation.statut != 'PENDING':
                return Response({'detail': 'Vous ne pouvez supprimer qu\'une réservation en attente.'}, status=status.HTTP_403_FORBIDDEN)
        response = super().destroy(request, *args, **kwargs)
        _release_vehicle(reservation.vehicle)
        return response

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        """Confirme la réservation et crée un contrat en statut RESERVE."""
        if request.user.role == 'CLIENT':
            return Response({'detail': 'Action réservée au personnel.'}, status=status.HTTP_403_FORBIDDEN)

        reservation = self.get_object()
        if reservation.statut != 'PENDING':
            return Response({'detail': 'Seule une réservation en attente peut être confirmée.'}, status=status.HTTP_400_BAD_REQUEST)

        from contracts.models import Contract
        import math
        diff = reservation.date_retour_prevue - reservation.date_sortie
        jours = max(1, math.ceil(diff.total_seconds() / (24 * 3600)))

        contract = Contract.objects.create(
            agency=reservation.agency,
            created_by=request.user if request.user.is_authenticated else None,
            vehicle=reservation.vehicle,
            client=reservation.client,
            date_sortie=reservation.date_sortie,
            date_retour_prevue=reservation.date_retour_prevue,
            jours=jours,
            prix_par_jour=reservation.prix_par_jour,
            caution=reservation.agency.caution_montant,
            km_sortie=int(request.data.get('km_sortie', 0) or 0),
            carburant_sortie=request.data.get('carburant_sortie', '4/8'),
            statut='RESERVE',
        )

        reservation.statut = 'CONFIRMED'
        reservation.save(update_fields=['statut'])

        return Response({
            'detail': 'Réservation confirmée, contrat créé.',
            'contract_id': contract.id,
        }, status=status.HTTP_201_CREATED)


class PdfJobViewSet(viewsets.ModelViewSet):
    serializer_class = PdfJobSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return PdfJob.objects.filter(contract__agency=self.request.user.agency).order_by('-id')

    def perform_create(self, serializer):
        contract = get_object_or_404(Contract, pk=self.request.data.get('contract'), agency=self.request.user.agency)
        job = serializer.save(contract=contract)
        thread = threading.Thread(target=process_pdf_job, args=(job.id,), daemon=True)
        thread.start()

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        job = self.get_object()
        if job.status != 'READY' or not job.pdf_file:
            return Response(
                {'detail': 'Le PDF n\'est pas encore prêt.', 'status': job.status},
                status=status.HTTP_400_BAD_REQUEST
            )
        response = HttpResponse(job.pdf_file.read(), content_type='application/pdf')
        filename = f"contrat_{job.contract.id}.pdf" if job.job_type == 'contract' else f"recu_{job.contract.id}.pdf"
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response
