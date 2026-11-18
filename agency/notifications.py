"""Génération des alertes (Notification) et API de consultation."""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import F
from django.utils import timezone
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from contracts.models import Contract
from fleet.models import Vehicle

from .alert_texts import lang_from_request, render_alert
from .models import Agency, Notification

logger = logging.getLogger(__name__)

EXPIRY_DAYS = 30
VIDANGE_MARGIN_KM = 500
RETURN_SOON_HOURS = 24


def _add(agency, created, dedup_key, code, params, **fields):
    """Crée l'alerte si elle n'existe pas. title/message sont stockés en français."""
    title, message = render_alert(code, params, "fr")
    notif, is_new = Notification.objects.get_or_create(
        agency=agency,
        dedup_key=dedup_key,
        defaults={
            **fields,
            "code": code,
            "params": params,
            "title": title,
            "message": message,
        },
    )
    if is_new:
        created.append(notif)


def generate_alerts_for_agency(agency, now=None):
    """Crée les alertes manquantes d'une agence. Retourne les nouvelles Notification."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    horizon = today + timedelta(days=EXPIRY_DAYS)
    created = []

    vehicles = Vehicle.objects.filter(
        agency=agency, is_deleted=False, is_archived=False
    )

    for field, kind in (
        ("date_assurance", "INSURANCE"),
        ("date_visite_technique", "VISITE"),
    ):
        prefix = "INSURANCE" if kind == "INSURANCE" else "VISITE"
        for v in vehicles.filter(**{f"{field}__lte": horizon}):
            d = getattr(v, field)
            days = (d - today).days
            expired = days < 0
            _add(
                agency,
                created,
                f"{kind}:{v.id}:{d.isoformat()}",
                f"{prefix}_{'EXPIRED' if expired else 'SOON'}",
                {
                    "plate": v.matricule,
                    "days": -days if expired else days,
                    "date": d.isoformat(),
                },
                kind=kind,
                level="CRITICAL" if expired or days <= 7 else "WARNING",
                vehicle=v,
            )

    for v in vehicles.filter(
        kilometrage__gte=F("prochain_vidange_km") - VIDANGE_MARGIN_KM
    ):
        _add(
            agency,
            created,
            f"VIDANGE:{v.id}:{v.prochain_vidange_km}",
            "VIDANGE",
            {"plate": v.matricule, "km": v.kilometrage, "next_km": v.prochain_vidange_km},
            kind="VIDANGE",
            level="CRITICAL" if v.kilometrage >= v.prochain_vidange_km else "WARNING",
            vehicle=v,
        )

    active = Contract.objects.filter(agency=agency, statut="EN_COURS").select_related(
        "vehicle", "client"
    )
    for c in active:
        due = c.date_retour_prevue
        key_day = timezone.localtime(due).date().isoformat()
        params = {
            "plate": c.vehicle.matricule,
            "contract": c.id,
            "client": f"{c.client.prenom} {c.client.nom}",
            "when": f"{timezone.localtime(due):%d/%m/%Y %H:%M}",
        }
        if due < now:
            _add(
                agency,
                created,
                f"RETURN_OVERDUE:{c.id}:{key_day}",
                "RETURN_OVERDUE",
                params,
                kind="RETURN_OVERDUE",
                level="CRITICAL",
                vehicle=c.vehicle,
                contract=c,
            )
        elif due <= now + timedelta(hours=RETURN_SOON_HOURS):
            _add(
                agency,
                created,
                f"RETURN_DUE:{c.id}:{key_day}",
                "RETURN_DUE",
                params,
                kind="RETURN_DUE",
                level="INFO",
                vehicle=c.vehicle,
                contract=c,
            )
    return created


def email_digest(agency, notifications):
    """Un seul e-mail récapitulatif par agence. Retourne True si envoyé."""
    if not agency.email or not notifications:
        return False
    body = "\n".join(f"- {n.title}\n  {n.message}" for n in notifications)
    send_mail(
        subject=f"[Krini] {len(notifications)} alerte(s) - {agency.nom_agence}",
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[agency.email],
        fail_silently=False,
    )
    Notification.objects.filter(pk__in=[n.pk for n in notifications]).update(
        emailed_at=timezone.now()
    )
    return True


def run_alerts(send_email=True, agencies=None):
    """Point d'entrée de la commande planifiée. Retourne {agency_id: nb_nouvelles}."""
    result = {}
    qs = agencies if agencies is not None else Agency.objects.filter(is_active=True)
    for agency in qs:
        created = generate_alerts_for_agency(agency)
        try:
            from fleet.geofence import check_geofences

            created = created + check_geofences(agency)
        except Exception:  # un souci GPS ne doit pas bloquer les alertes classiques
            logger.exception("geofence_check_failed", extra={"agency_id": agency.id})
        if send_email:
            try:
                email_digest(agency, created)
            except Exception:  # un SMTP en panne ne doit pas bloquer les autres agences
                logger.exception("alert_email_failed", extra={"agency_id": agency.id})
        result[agency.id] = len(created)
    return result


class NotificationSerializer(serializers.ModelSerializer):
    title = serializers.SerializerMethodField()
    message = serializers.SerializerMethodField()
    vehicle_matricule = serializers.CharField(
        source="vehicle.matricule", read_only=True, default=None
    )

    class Meta:
        model = Notification
        fields = [
            "id",
            "kind",
            "level",
            "title",
            "message",
            "vehicle",
            "vehicle_matricule",
            "contract",
            "is_read",
            "created_at",
        ]
        read_only_fields = fields

    def _rendered(self, obj):
        """(titre, message) dans la langue de la requête ; repli sur le texte stocké."""
        lang = lang_from_request(self.context.get("request"))
        if obj.code and lang != "fr":
            rendered = render_alert(obj.code, obj.params, lang)
            if rendered:
                return rendered
        return obj.title, obj.message

    def get_title(self, obj):
        return self._rendered(obj)[0]

    def get_message(self, obj):
        return self._rendered(obj)[1]


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["kind", "level", "is_read"]

    def get_queryset(self):
        user = self.request.user
        qs = Notification.objects.select_related("vehicle")
        return qs if user.is_superuser else qs.filter(agency=user.agency)

    @action(detail=False, methods=["get"])
    def unread_count(self, request):
        return Response({"count": self.get_queryset().filter(is_read=False).count()})

    @action(detail=True, methods=["post"])
    def mark_read(self, request, pk=None):
        n = self.get_object()
        n.is_read = True
        n.save(update_fields=["is_read"])
        return Response({"id": n.id, "is_read": True})

    @action(detail=False, methods=["post"])
    def mark_all_read(self, request):
        updated = self.get_queryset().filter(is_read=False).update(is_read=True)
        return Response({"updated": updated})

    @action(detail=False, methods=["post"])
    def refresh(self, request):
        """Régénère les alertes de l'agence à la demande (sans e-mail)."""
        if not request.user.agency:
            return Response({"detail": "Compte non lié à une agence."}, status=400)
        created = generate_alerts_for_agency(request.user.agency)
        return Response({"created": len(created)})
