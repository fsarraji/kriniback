from django.http import HttpResponse
from django.template.loader import get_template
from weasyprint import HTML
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
import os
import tempfile
import base64
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont
import math
from io import BytesIO
import qrcode
import barcode
from barcode.writer import ImageWriter
from .models import Contract, ContractDamage
from .serializers import ContractSerializer

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

class ContractViewSet(viewsets.ModelViewSet):
    serializer_class = ContractSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.is_superuser:
            return Contract.objects.all().order_by('-date_creation')
        return Contract.objects.filter(agency=self.request.user.agency).order_by('-date_creation')

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

        # ✅ Remettre le véhicule en disponible
        vehicle = contract.vehicle
        vehicle.statut = 'Available'
        vehicle.kilometrage = km_retour_int  # Mettre à jour le kilométrage du véhicule
        vehicle.save()

        return Response({
            'detail': 'تم إغلاق العقد وإرجاع السيارة بنجاح.',
            'reste_a_payer': str(contract.reste_a_payer),
            'date_retour_effective': contract.date_retour_effective.isoformat() if contract.date_retour_effective else None,
            'km_parcourus': contract.km_retour - contract.km_sortie,
        }, status=status.HTTP_200_OK)
        
    @action(detail=True, methods=['get'])
    def print_contract(self, request, pk=None):
        try:
            # 1. جلب العقد المطلوب
            contract = self.get_object()
            agency = request.user.agency
            with_cachet = request.GET.get('with_cachet', 'false').lower() == 'true'

            # 2. تحديد مسار القالب (HTML Template)
            template_path = 'contracts/contract_pdf.html'
            car_diagram_path = os.path.join(settings.BASE_DIR, 'car_damage_diagram.png')
            depart_damages = contract.damages.filter(type='DEPART')
            retour_damages = contract.damages.filter(type='RETOUR')

            # 3. Generating diagram base64
            def build_diagram_base64(damages, dot_color='red'):
                if not os.path.exists(car_diagram_path):
                    return ""
                try:
                    img = Image.open(car_diagram_path).convert('RGB')
                    # Significant reduction: 250px is enough for A4 printing
                    img.thumbnail((250, 250), Image.Resampling.LANCZOS)
                    if damages.exists():
                        draw = ImageDraw.Draw(img)
                        w, h = img.size
                        try:
                            font = ImageFont.truetype("arial.ttf", size=int(w * 0.08))
                        except IOError:
                            font = ImageFont.load_default()
                        for i, dmg in enumerate(damages):
                            x_px = (dmg.x / 100.0) * w
                            y_px = (dmg.y / 100.0) * h
                            radius = w * 0.04
                            draw.ellipse((x_px - radius, y_px - radius, x_px + radius, y_px + radius), fill=dot_color)
                            draw.text((x_px, y_px), str(i + 1), fill='white', font=font, anchor="mm")
                    buffered = BytesIO()
                    # JPEG is MUCH lighter in base64 than PNG
                    img.save(buffered, format="JPEG", quality=75, optimize=True)
                    encoded = base64.b64encode(buffered.getvalue()).decode('utf-8')
                    return f"data:image/jpeg;base64,{encoded}"
                except Exception:
                    return ""

            car_diagram_base64 = build_diagram_base64(depart_damages, dot_color='red')
            retour_diagram_base64 = build_diagram_base64(retour_damages, dot_color='orange')
                
            # 4. Generating fuel gauges
            fuel_depart_base64 = generate_fuel_gauge_image(contract.carburant_sortie)
            fuel_retour_base64 = generate_fuel_gauge_image(contract.carburant_retour)

            # 5. Generating QR code
            whatsapp_qr_base64 = ""
            if agency.telephone:
                phone = agency.telephone.replace(' ', '').replace('-', '').replace('.', '')
                if phone.startswith('0'):
                    phone = '212' + phone[1:]
                elif phone.startswith('+'):
                    phone = phone[1:]
                
                if phone:
                    wa_link = f"https://wa.me/{phone}"
                    try:
                        qr = qrcode.QRCode(version=1, box_size=10, border=1)
                        qr.add_data(wa_link)
                        qr.make(fit=True)
                        qr_img = qr.make_image(fill_color="#111827", back_color="white")
                        qr_buf = BytesIO()
                        qr_img.save(qr_buf, format="PNG")
                        qr_b64 = base64.b64encode(qr_buf.getvalue()).decode('utf-8')
                        whatsapp_qr_base64 = f"data:image/png;base64,{qr_b64}"
                    except Exception:
                        pass

            # 6. Generating Barcode
            contract_barcode_base64 = ""
            try:
                code_str = str(contract.id).zfill(4)
                COD128 = barcode.get_barcode_class('code128')
                bar = COD128(code_str, writer=ImageWriter())
                bar_buf = BytesIO()
                bar.write(bar_buf, options={"write_text": False, "module_height": 5.0, "module_width": 0.25})
                bar_b64 = base64.b64encode(bar_buf.getvalue()).decode('utf-8')
                contract_barcode_base64 = f"data:image/png;base64,{bar_b64}"
            except Exception:
                pass

            # 7. Calculating Finances
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

            # 8. Generating cachet base64
            cachet_base64 = ""
            if with_cachet and agency.cachet_signature:
                try:
                    img = Image.open(agency.cachet_signature).convert('RGBA')
                    img.thumbnail((300, 150), Image.Resampling.LANCZOS)
                    buffered = BytesIO()
                    img.save(buffered, format="PNG", optimize=True)
                    encoded = base64.b64encode(buffered.getvalue()).decode('utf-8')
                    cachet_base64 = f"data:image/png;base64,{encoded}"
                except Exception as e:
                    print("Error loading cachet: ", e)
                    pass

            # 9. Building Context
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

            # 10. Rendering Django HTML Template
            response = HttpResponse(content_type='application/pdf')
            response['Content-Disposition'] = f'inline; filename="Contrat_{contract.id}.pdf"'
            template = get_template(template_path)
            html = template.render(context)

            # 11. Generating PDF
            pdf_file = HTML(string=html).write_pdf()
            response.write(pdf_file)
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
        template_path = 'contracts/reservation_receipt_pdf.html'

        # Generate WhatsApp QR code
        whatsapp_qr_base64 = ""
        if agency.telephone:
            phone = agency.telephone.replace(' ', '').replace('-', '').replace('.', '')
            if phone.startswith('0'):
                phone = '212' + phone[1:]
            elif phone.startswith('+'):
                phone = phone[1:]
            if phone:
                wa_link = f"https://wa.me/{phone}"
                try:
                    qr = qrcode.QRCode(version=1, box_size=10, border=1)
                    qr.add_data(wa_link)
                    qr.make(fit=True)
                    qr_img = qr.make_image(fill_color="#111827", back_color="white")
                    qr_buf = BytesIO()
                    qr_img.save(qr_buf, format="PNG")
                    qr_b64 = base64.b64encode(qr_buf.getvalue()).decode('utf-8')
                    whatsapp_qr_base64 = f"data:image/png;base64,{qr_b64}"
                except Exception as e:
                    print("Error generating QR code:", e)

        context = {
            'contract': contract,
            'agency': agency,
            'whatsapp_qr_base64': whatsapp_qr_base64,
        }

        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="Recu_Reservation_{contract.id}.pdf"'

        template = get_template(template_path)
        html = template.render(context)

        try:
            pdf_file = HTML(string=html).write_pdf()
            response.write(pdf_file)
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print("PDF GENERATION ERROR: ", error_details)
            return Response(f'عذراً، حدث خطأ أثناء توليد ملف الـ PDF: {str(e)}\nDetails: {error_details}', status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        return response
