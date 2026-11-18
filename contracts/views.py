import base64
import logging
import math
import os
import threading
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from io import BytesIO
from time import perf_counter

import barcode
import qrcode
import requests
from barcode.writer import ImageWriter
from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import get_template
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from PIL import Image, ImageDraw, ImageFont
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .bookings import BookingRequestViewSet  # noqa: F401 (ré-export)
from .bookings import ReservationViewSet
from .models import Contract, ContractDamage, PdfJob, Reservation, _release_vehicle
from .serializers import ContractSerializer, PdfJobSerializer

logger = logging.getLogger(__name__)

CONTRACT_TEMPLATE_PATHS = {
    "standard": "contracts/contract_pdf.html",
    "minimal": "contracts/contract_pdf_minimal.html",
}


def resolve_contract_template_path(template):
    return CONTRACT_TEMPLATE_PATHS.get(template, CONTRACT_TEMPLATE_PATHS["standard"])


_pdf_session = requests.Session()


def generate_pdf(html_string):
    service_url = settings.PDF_SERVICE_URL.rstrip("/") + "/convert"
    html_size = len(html_string.encode("utf-8"))
    logger.info("pdf_service_call", extra={"url": service_url, "html_size": html_size})

    started = perf_counter()
    try:
        resp = _pdf_session.post(
            service_url, json={"html": html_string}, timeout=(10, 90)
        )
    except requests.RequestException as e:
        logger.error(
            "pdf_service_http_error",
            extra={
                "error": str(e),
                "html_size": html_size,
            },
        )
        raise
    http_time = perf_counter() - started

    resp.raise_for_status()

    pdf = resp.content

    if not pdf:
        raise RuntimeError("PDF service returned an empty response")

    if not pdf.startswith(b"%PDF"):
        raise RuntimeError("PDF service returned an invalid PDF")

    logger.info(
        "pdf_generated",
        extra={
            "html_size": html_size,
            "pdf_size": len(pdf),
            "http_time": round(http_time, 3),
        },
    )

    return pdf


def generate_fuel_gauge_image(level_str):
    if not level_str:
        return None
    try:
        level = int(level_str.split("/")[0])
    except:
        level = 0
    levels = 8

    # Smaller canvas (150x90) to save memory
    width, height = 150, 90
    # Use white background (no alpha) to allow JPEG conversion
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    cx, cy = 75, 75
    r = 60

    # Track background
    draw.arc([cx - r, cy - r, cx + r, cy + r], 180, 0, fill="#e2e8f0", width=3)

    try:
        font = ImageFont.truetype("arial.ttf", size=14)
        font_small = ImageFont.truetype("arial.ttf", size=10)
    except IOError:
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()

    draw.text((20, 80), "E", fill="#0f172a", font=font, anchor="mm")
    draw.text((130, 80), "F", fill="#0f172a", font=font, anchor="mm")

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

        draw.line((x1, y1, x2, y2), fill="#94a3b8", width=2 if is_major else 1)

        if is_major:
            # Shift text towards center
            tx = cx + (r - 20) * math.cos(rad)
            ty = cy - (r - 20) * math.sin(rad)
            draw.text((tx, ty), str(i), fill="#64748b", font=font_small, anchor="mm")

    level_val = min(max(level, 0), levels)
    angle = 180 - (level_val / levels) * 180
    rad = math.radians(angle)
    pointer_len = r - 5
    px = cx + pointer_len * math.cos(rad)
    py = cy - pointer_len * math.sin(rad)

    # Drawing needle
    draw.line((cx, cy, px, py), fill="#ef4444", width=3)
    # Center dot
    draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill="#1e293b")

    buffered = BytesIO()
    # JPEG is extremely compact for such simple graphics
    img.save(buffered, format="JPEG", quality=70, optimize=True)
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{img_str}"


@lru_cache(maxsize=1)
def _get_car_diagram_source():
    car_diagram_path = os.path.join(settings.BASE_DIR, "car_damage_diagram.png")
    if not os.path.exists(car_diagram_path):
        return None
    img = Image.open(car_diagram_path).convert("RGB")
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
            draw.ellipse(
                (x_px - radius, y_px - radius, x_px + radius, y_px + radius),
                fill=dot_color,
            )
            draw.text((x_px, y_px), str(i + 1), fill="white", font=font, anchor="mm")
        buffered = BytesIO()
        img.save(buffered, format="JPEG", quality=75, optimize=True)
        encoded = base64.b64encode(buffered.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{encoded}"
    except Exception:
        return ""


def _get_diagram_base64(damages, dot_color):
    signature = tuple((float(d.x), float(d.y)) for d in damages)
    return _damage_diagram_base64(signature, dot_color)


def _normalize_wa_phone(telephone):
    if not telephone:
        return None
    phone = telephone.replace(" ", "").replace("-", "").replace(".", "")
    if phone.startswith("0"):
        phone = "212" + phone[1:]
    elif phone.startswith("+"):
        phone = phone[1:]
    return phone or None


def _generate_whatsapp_qr_base64(wa_link):
    qr = qrcode.QRCode(version=1, box_size=10, border=1)
    qr.add_data(wa_link)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="#111827", back_color="white")
    qr_buf = BytesIO()
    qr_img.save(qr_buf, format="PNG")
    qr_b64 = base64.b64encode(qr_buf.getvalue()).decode("utf-8")
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
    img = Image.open(image_field).convert("RGBA")
    img.thumbnail((300, 150), Image.Resampling.LANCZOS)
    buffered = BytesIO()
    img.save(buffered, format="PNG", optimize=True)
    encoded = base64.b64encode(buffered.getvalue()).decode("utf-8")
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
        COD128 = barcode.get_barcode_class("code128")
        bar = COD128(code_str, writer=ImageWriter())
        bar_buf = BytesIO()
        bar.write(
            bar_buf,
            options={"write_text": False, "module_height": 5.0, "module_width": 0.25},
        )
        bar_b64 = base64.b64encode(bar_buf.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{bar_b64}"
    except Exception:
        return ""


def build_contract_pdf(contract, agency, with_cachet=False, template="standard"):
    template_path = resolve_contract_template_path(template)
    start = perf_counter()

    depart_damages = contract.damages.filter(type="DEPART")
    retour_damages = contract.damages.filter(type="RETOUR")

    assets_start = perf_counter()

    car_diagram_base64 = _get_diagram_base64(depart_damages, "red")
    retour_diagram_base64 = _get_diagram_base64(retour_damages, "orange")

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
            logger.warning(
                "cachet_generation_error",
                extra={"agency_id": agency.id, "error": str(e)},
            )

    assets_time = perf_counter() - assets_start

    # Calculating Finances
    km_parcourus = (
        contract.km_retour - contract.km_sortie if contract.km_retour else None
    )
    agency_km_extra_active = agency.km_extra_active
    agency_km_par_jour = agency.km_par_jour
    km_tarif_extra = float(
        contract.vehicle.tarif_km_extra or agency.km_tarif_extra_defaut or 1.5
    )
    km_inclus_total = (
        (agency_km_par_jour * contract.jours) if agency_km_extra_active else None
    )
    km_supplementaires = None
    montant_km_extra = None
    if (
        agency_km_extra_active
        and km_parcourus is not None
        and km_inclus_total is not None
    ):
        km_supplementaires = max(0, km_parcourus - km_inclus_total)
        montant_km_extra = (
            round(km_supplementaires * km_tarif_extra, 2)
            if km_supplementaires > 0
            else 0
        )

    # Building Context
    context = {
        "contract": contract,
        "agency": agency,
        "car_diagram_base64": car_diagram_base64,
        "retour_diagram_base64": retour_diagram_base64,
        "depart_damages": depart_damages,
        "retour_damages": retour_damages,
        "fuel_depart_base64": fuel_depart_base64,
        "fuel_retour_base64": fuel_retour_base64,
        "whatsapp_qr_base64": whatsapp_qr_base64,
        "contract_barcode_base64": contract_barcode_base64,
        "km_parcourus": km_parcourus,
        "agency_km_extra_active": agency_km_extra_active,
        "agency_km_par_jour": agency_km_par_jour,
        "km_tarif_extra": km_tarif_extra,
        "km_inclus_total": km_inclus_total,
        "km_supplementaires": km_supplementaires,
        "montant_km_extra": montant_km_extra,
        "cachet_base64": cachet_base64,
    }

    template = get_template(template_path)
    html_start = perf_counter()
    html = template.render(context)
    template_time = perf_counter() - html_start

    pdf = generate_pdf(html)

    total_time = perf_counter() - start

    logger.info(
        "contract_pdf_completed",
        extra={
            "contract_id": contract.id,
            "assets_time": round(assets_time, 3),
            "template_time": round(template_time, 3),
            "total_time": round(total_time, 3),
            "html_size": len(html.encode("utf-8")),
            "pdf_size": len(pdf),
        },
    )

    return pdf


def build_receipt_pdf(contract, agency):
    template_path = "contracts/reservation_receipt_pdf.html"
    start = perf_counter()

    try:
        whatsapp_qr_base64 = get_whatsapp_qr_base64(agency)
    except Exception:
        whatsapp_qr_base64 = ""

    context = {
        "contract": contract,
        "agency": agency,
        "whatsapp_qr_base64": whatsapp_qr_base64,
    }

    template = get_template(template_path)
    html = template.render(context)
    pdf = generate_pdf(html)

    total_time = perf_counter() - start

    logger.info(
        "receipt_pdf_completed",
        extra={
            "contract_id": contract.id,
            "total_time": round(total_time, 3),
            "html_size": len(html.encode("utf-8")),
            "pdf_size": len(pdf),
        },
    )

    return pdf


def process_pdf_job(job_id):
    try:
        job = PdfJob.objects.get(id=job_id)
        job.status = "PROCESSING"
        job.save(update_fields=["status", "updated_at"])

        contract = job.contract
        agency = contract.agency

        if job.job_type == "contract":
            pdf_bytes = build_contract_pdf(
                contract, agency, job.with_cachet, job.template
            )
            filename = f"contrat_{contract.id}.pdf"
        else:
            pdf_bytes = build_receipt_pdf(contract, agency)
            filename = f"recu_{contract.id}.pdf"

        job.pdf_file.save(filename, ContentFile(pdf_bytes), save=False)
        job.status = "READY"
        job.error_message = None
        job.save()
    except Exception as e:
        logger.exception("pdf_job_failed", extra={"job_id": job_id})
        PdfJob.objects.filter(id=job_id).update(
            status="ERROR",
            error_message=str(e),
        )


class ContractViewSet(viewsets.ModelViewSet):
    serializer_class = ContractSerializer
    permission_classes = [permissions.IsAuthenticated]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["statut", "client", "vehicle"]
    search_fields = [
        "client__nom",
        "client__prenom",
        "client__telephone",
        "vehicle__matricule",
        "vehicle__marque__name",
        "vehicle__modele__name",
    ]
    ordering_fields = [
        "date_creation",
        "montant_total",
        "date_sortie",
        "date_retour_prevue",
    ]

    def get_queryset(self):
        base = (
            Contract.objects.select_related(
                "client",
                "vehicle__marque",
                "vehicle__modele",
                "deuxieme_chauffeur",
                "agency",
                "created_by",
            )
            .prefetch_related("damages")
            .order_by("-date_creation")
        )
        if self.request.user.is_superuser:
            return base
        return base.filter(agency=self.request.user.agency)

    def perform_create(self, serializer):
        from .availability import assert_vehicle_free

        data = serializer.validated_data
        with transaction.atomic():
            # Verrou sur le véhicule + re-contrôle : évite le double-booking concurrent.
            assert_vehicle_free(
                data["vehicle"], data["date_sortie"], data["date_retour_prevue"]
            )
            serializer.save(
                agency=self.request.user.agency, created_by=self.request.user
            )

    def perform_update(self, serializer):
        from .availability import assert_vehicle_free

        instance = serializer.instance
        data = serializer.validated_data
        period_changed = any(
            key in data for key in ("vehicle", "date_sortie", "date_retour_prevue")
        )
        with transaction.atomic():
            if period_changed and instance.statut in ("RESERVE", "EN_COURS"):
                # Même verrou de ligne qu'à la création : pas de double réservation
                # lorsqu'on déplace un contrat vers un autre véhicule ou d'autres dates.
                assert_vehicle_free(
                    data.get("vehicle", instance.vehicle),
                    data.get("date_sortie", instance.date_sortie),
                    data.get("date_retour_prevue", instance.date_retour_prevue),
                    exclude_contract=instance,
                )
            serializer.save()

    # مسار مخصص لإغلاق العقد وإرجاع السيارة
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def return_vehicle(self, request, pk=None):
        contract = self.get_object()

        # التأكد من أن العقد لم ينتهِ بالفعل
        if contract.statut == "TERMINE":
            return Response(
                {"detail": "هذا العقد منتهي بالفعل."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if contract.statut == "ANNULE":
            return Response(
                {"detail": "لا يمكن إغلاق عقد ملغى."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # --- البيانات الأساسية ---
        km_retour = request.data.get("km_retour")
        carburant_retour = request.data.get("carburant_retour")
        degats_retour = request.data.get("degats_retour", "")
        date_retour_effective = request.data.get("date_retour_effective")

        if not km_retour:
            return Response(
                {"detail": "يجب إدخال الكيلومتراج عند الإرجاع."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            km_retour_int = int(km_retour)
        except (ValueError, TypeError):
            return Response(
                {"detail": "الكيلومتراج يجب أن يكون رقماً صحيحاً."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if km_retour_int < contract.km_sortie:
            return Response(
                {
                    "detail": f"كيلومتراج الإرجاع ({km_retour_int}) لا يمكن أن يكون أقل من كيلومتراج الخروج ({contract.km_sortie})."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # --- تحديث الحقول الأساسية ---
        contract.km_retour = km_retour_int
        contract.carburant_retour = carburant_retour
        contract.degats_retour = degats_retour

        if date_retour_effective:
            from django.utils.dateparse import parse_datetime

            parsed_date = parse_datetime(date_retour_effective)
            contract.date_retour_effective = (
                parsed_date if parsed_date else timezone.now()
            )
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
        payment_amount = request.data.get("payment_amount", 0)
        payment_method = request.data.get("payment_method", "Espèce")

        try:
            payment_amount = Decimal(str(payment_amount or 0))
        except (InvalidOperation, ValueError):
            transaction.set_rollback(True)
            return Response(
                {"detail": "Montant du paiement invalide."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if payment_amount > 0:
            from payments.models import Payment

            Payment.objects.create(
                agency=contract.agency,
                contract=contract,
                user=request.user,
                amount=payment_amount,
                payment_method=payment_method,
                notes="Règlement final lors de la clôture du contrat",
            )
            # Recharger pour avoir les montants à jour (montant_paye/reste_a_payer)
            contract.refresh_from_db()

        # --- ÉTAT DES ACCESSOIRES AU RETOUR ---
        accessories_retour = request.data.get("accessories_retour", {})
        if isinstance(accessories_retour, dict):
            contract.roue_secours_retour = accessories_retour.get("roue_secours", False)
            contract.cric_retour = accessories_retour.get("cric", False)
            contract.manivelle_retour = accessories_retour.get("manivelle", False)
            contract.gilet_retour = accessories_retour.get("gilet", False)
            contract.triangle_retour = accessories_retour.get("triangle", False)
            contract.extincteur_retour = accessories_retour.get("extincteur", False)
            contract.papiers_retour = accessories_retour.get("papiers", False)
            contract.cles_retour = accessories_retour.get("cles", False)

        # --- نقاط الأضرار عند الإرجاع ---
        damages_retour = request.data.get("damages_retour", [])
        if isinstance(damages_retour, list):
            # حذف أي أضرار سابقة من نوع RETOUR لهذا العقد
            contract.damages.filter(type="RETOUR").delete()
            for dmg in damages_retour:
                try:
                    ContractDamage.objects.create(
                        contract=contract,
                        type="RETOUR",
                        x=float(dmg.get("x", 0)),
                        y=float(dmg.get("y", 0)),
                        description=dmg.get("description", ""),
                    )
                except (ValueError, TypeError):
                    pass

        contract.statut = "TERMINE"
        contract.save()

        # ✅ Remettre le véhicule en disponible (si aucune autre location en cours)
        vehicle = contract.vehicle
        vehicle.kilometrage = km_retour_int  # Mettre à jour le kilométrage du véhicule
        vehicle.save(update_fields=["kilometrage"])
        _release_vehicle(vehicle)

        return Response(
            {
                "detail": "تم إغلاق العقد وإرجاع السيارة بنجاح.",
                "reste_a_payer": str(contract.reste_a_payer),
                "date_retour_effective": contract.date_retour_effective.isoformat()
                if contract.date_retour_effective
                else None,
                "km_parcourus": contract.km_retour - contract.km_sortie,
            },
            status=status.HTTP_200_OK,
        )

    # مسار مخصص لتمديد العقد (Prolonger le contrat)
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def prolong(self, request, pk=None):
        contract = self.get_object()

        if contract.statut == "TERMINE":
            return Response(
                {"detail": "هذا العقد منتهي بالفعل."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if contract.statut == "ANNULE":
            return Response(
                {"detail": "لا يمكن تمديد عقد ملغى."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        date_retour_prevue = request.data.get("date_retour_prevue")
        if not date_retour_prevue:
            return Response(
                {"detail": "يجب تحديد تاريخ الإرجاع الجديد."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from django.utils.dateparse import parse_datetime

            from .serializers import _make_aware

            new_retour = _make_aware(parse_datetime(date_retour_prevue))
        except Exception:
            new_retour = None
        if new_retour is None:
            return Response(
                {"detail": "تاريخ الإرجاع غير صالح."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_retour <= contract.date_retour_prevue:
            return Response(
                {
                    "detail": "تاريخ الإرجاع الجديد يجب أن يكون بعد تاريخ الإرجاع الحالي."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # --- Disponibilité sur la nouvelle période (verrou de ligne) ---
        from .availability import assert_vehicle_free

        assert_vehicle_free(
            contract.vehicle,
            contract.date_sortie,
            new_retour,
            exclude_contract=contract,
        )

        # --- Recalcul de la durée et du montant ---
        diff = new_retour - contract.date_sortie
        new_jours = math.ceil(diff.total_seconds() / (24 * 3600))
        if new_jours < 1:
            new_jours = 1

        contract.date_retour_prevue = new_retour
        contract.jours = new_jours
        # Le save() recalcule montant_total et reste_a_payer selon la saisonnalité.
        contract.save()

        return Response(
            {
                "detail": "تم تمديد العقد بنجاح.",
                "date_retour_prevue": contract.date_retour_prevue.isoformat(),
                "jours": contract.jours,
                "montant_total": str(contract.montant_total),
                "reste_a_payer": str(contract.reste_a_payer),
            },
            status=status.HTTP_200_OK,
        )

    # مسار مخصص لإلغاء عقد/حجز من نوع RESERVE (réservation agence)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        contract = self.get_object()

        if contract.statut != "RESERVE":
            return Response(
                {
                    "detail": "Seule une réservation (contrat en statut Réservé) peut être annulée."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Seul le gérant (OWNER) ou un superadmin peut annuler.
        if not (request.user.is_superuser or request.user.role == "OWNER"):
            return Response(
                {
                    "detail": "Seul le gérant de la société peut annuler une réservation."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        contract.statut = "ANNULE"
        # Le save() libère le véhicule (mise en disponible).
        contract.save()

        return Response(
            {
                "detail": "Réservation annulée (contrat annulé).",
                "statut": contract.statut,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"])
    def print_contract(self, request, pk=None):
        try:
            contract = self.get_object()
            agency = contract.agency
            with_cachet = request.GET.get("with_cachet", "false").lower() == "true"
            template = request.GET.get("template", "standard")

            pdf_file = build_contract_pdf(contract, agency, with_cachet, template)

            response = HttpResponse(pdf_file, content_type="application/pdf")
            response[
                "Content-Disposition"
            ] = f'inline; filename="Contrat_{contract.id}.pdf"'
            return response

        except Exception:
            logger.exception("print_contract_failed", extra={"contract_id": pk})
            return HttpResponse("عذراً، حدث خطأ أثناء توليد ملف الـ PDF.", status=500)

    @action(detail=True, methods=["get"])
    def print_reservation_receipt(self, request, pk=None):
        contract = self.get_object()
        agency = contract.agency

        try:
            pdf_file = build_receipt_pdf(contract, agency)
        except Exception:
            # Ne jamais renvoyer la trace d'exécution au client.
            logger.exception("print_receipt_failed", extra={"contract_id": pk})
            return Response(
                {"detail": "Erreur lors de la génération du PDF."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response = HttpResponse(pdf_file, content_type="application/pdf")
        response[
            "Content-Disposition"
        ] = f'inline; filename="Recu_Reservation_{contract.id}.pdf"'
        return response


class PdfJobViewSet(viewsets.ModelViewSet):
    serializer_class = PdfJobSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return PdfJob.objects.filter(
            contract__agency=self.request.user.agency
        ).order_by("-id")

    def perform_create(self, serializer):
        contract = get_object_or_404(
            Contract,
            pk=self.request.data.get("contract"),
            agency=self.request.user.agency,
        )
        job = serializer.save(contract=contract)
        # Mode « inline » (défaut) : traitement immédiat en thread. Avec un worker
        # durable (manage.py process_pdf_jobs --loop), mettre PDF_JOBS_INLINE=false :
        # le job reste PENDING et survit aux redémarrages.
        if settings.PDF_JOBS_INLINE:
            threading.Thread(
                target=process_pdf_job, args=(job.id,), daemon=True
            ).start()

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        job = self.get_object()
        if job.status != "READY" or not job.pdf_file:
            return Response(
                {"detail": "Le PDF n'est pas encore prêt.", "status": job.status},
                status=status.HTTP_400_BAD_REQUEST,
            )
        response = HttpResponse(job.pdf_file.read(), content_type="application/pdf")
        filename = (
            f"contrat_{job.contract.id}.pdf"
            if job.job_type == "contract"
            else f"recu_{job.contract.id}.pdf"
        )
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response
