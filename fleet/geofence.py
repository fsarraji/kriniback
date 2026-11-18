"""Zones autorisées (géorepérage) : alerte quand un véhicule loué sort de toutes les zones.

Principe : une agence définit des zones circulaires (centre + rayon). Tant qu'elle n'en
a aucune d'active, rien n'est contrôlé. Dès qu'elle en a, chaque véhicule sous contrat
EN_COURS dont la dernière position GPS est hors de toutes les zones déclenche une
alerte (Notification de type GEOFENCE), au plus une par contrat et par jour.
"""
import logging
from math import asin, cos, radians, sin, sqrt

from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from car_rental_backend.permissions import IsAgencyStaff

from .models import Geofence
from .traccar import (
    TraccarError,
    TraccarNotConfigured,
    normalize_position,
    positions_by_device,
)

logger = logging.getLogger(__name__)

EARTH_RADIUS_M = 6_371_000
MIN_RADIUS_M = 50
MAX_RADIUS_M = 2_000_000  # 2 000 km


def haversine_m(lat1, lon1, lat2, lon2):
    """Distance en mètres entre deux points GPS (grand cercle)."""
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def nearest_zone(lat, lon, zones):
    """(zone, distance_au_bord_en_m) de la zone la plus proche. Distance <= 0 : à l'intérieur."""
    best = None
    for zone in zones:
        margin = haversine_m(lat, lon, zone.latitude, zone.longitude) - zone.radius_m
        if best is None or margin < best[1]:
            best = (zone, margin)
    return best


def vehicles_outside(agency, positions=None):
    """Véhicules sous contrat EN_COURS hors de toutes les zones actives de l'agence.

    `positions` : {device_id: position Traccar} (récupérées si None). Retourne une liste
    de dicts ; liste vide si aucune zone active, si Traccar n'est pas configuré/joignable.
    """
    from contracts.models import Contract

    zones = list(Geofence.objects.filter(agency=agency, is_active=True))
    if not zones:
        return []

    if positions is None:
        try:
            positions = positions_by_device(agency=agency)
        except (TraccarNotConfigured, TraccarError):
            logger.warning(
                "geofence_positions_unavailable", extra={"agency_id": agency.id}
            )
            return []

    outside = []
    contracts = Contract.objects.filter(
        agency=agency, statut="EN_COURS"
    ).select_related("vehicle", "client")
    for contract in contracts:
        device_id = contract.vehicle.traccar_device_id
        position = normalize_position(positions.get(device_id)) if device_id else None
        if not position:
            continue  # pas de fix GPS exploitable : on ne peut rien affirmer
        lat, lon = position["latitude"], position["longitude"]
        zone, margin = nearest_zone(lat, lon, zones)
        if margin > 0:
            outside.append(
                {
                    "vehicle_id": contract.vehicle_id,
                    "matricule": contract.vehicle.matricule,
                    "contract_id": contract.id,
                    "client": f"{contract.client.prenom} {contract.client.nom}",
                    "latitude": lat,
                    "longitude": lon,
                    "nearest_zone": zone.name,
                    "distance_outside_m": round(margin),
                }
            )
    return outside


def check_geofences(agency, positions=None, now=None):
    """Crée les alertes GEOFENCE manquantes (1 par contrat et par jour). Retourne les nouvelles."""
    from agency.alert_texts import render_alert
    from agency.models import Notification

    now = now or timezone.now()
    day = timezone.localdate(now).isoformat()
    created = []
    for item in vehicles_outside(agency, positions=positions):
        km = item["distance_outside_m"] / 1000
        params = {
            "plate": item["matricule"],
            "contract": item["contract_id"],
            "client": item["client"],
            "km": f"{km:.1f}",
            "zone": item["nearest_zone"],
        }
        title, message = render_alert("GEOFENCE", params, "fr")
        notif, is_new = Notification.objects.get_or_create(
            agency=agency,
            dedup_key=f"GEOFENCE:{item['contract_id']}:{day}",
            defaults={
                "kind": "GEOFENCE",
                "level": "CRITICAL",
                "code": "GEOFENCE",
                "params": params,
                "title": title,
                "message": message,
                "vehicle_id": item["vehicle_id"],
                "contract_id": item["contract_id"],
            },
        )
        if is_new:
            created.append(notif)
    return created


class GeofenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Geofence
        fields = [
            "id",
            "name",
            "latitude",
            "longitude",
            "radius_m",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate_latitude(self, value):
        if not -90 <= value <= 90:
            raise serializers.ValidationError(
                "La latitude doit être comprise entre -90 et 90."
            )
        return value

    def validate_longitude(self, value):
        if not -180 <= value <= 180:
            raise serializers.ValidationError(
                "La longitude doit être comprise entre -180 et 180."
            )
        return value

    def validate_radius_m(self, value):
        if not MIN_RADIUS_M <= value <= MAX_RADIUS_M:
            raise serializers.ValidationError(
                f"Le rayon doit être compris entre {MIN_RADIUS_M} m et {MAX_RADIUS_M // 1000} km."
            )
        return value


class GeofenceViewSet(viewsets.ModelViewSet):
    """Zones autorisées de l'agence. Lecture : personnel ; écriture : gérant uniquement."""

    serializer_class = GeofenceSerializer
    permission_classes = [IsAgencyStaff]
    pagination_class = None

    def get_queryset(self):
        qs = Geofence.objects.all()
        user = self.request.user
        return qs if user.is_superuser else qs.filter(agency=user.agency)

    def _require_owner(self):
        user = self.request.user
        if not (user.is_superuser or user.role == "OWNER"):
            raise PermissionDenied("Seul le gérant peut modifier les zones autorisées.")

    def perform_create(self, serializer):
        self._require_owner()
        agency = self.request.user.agency
        if agency is None:
            raise serializers.ValidationError(
                {"detail": "Compte non lié à une agence."}
            )
        serializer.save(agency=agency)

    def perform_update(self, serializer):
        self._require_owner()
        serializer.save()

    def perform_destroy(self, instance):
        self._require_owner()
        instance.delete()

    @action(detail=False, methods=["get"])
    def status(self, request):
        """Véhicules loués actuellement hors de toutes les zones actives (position en direct)."""
        agency = request.user.agency
        if agency is None:
            return Response({"zones": 0, "outside": []})
        zones = Geofence.objects.filter(agency=agency, is_active=True).count()
        return Response({"zones": zones, "outside": vehicles_outside(agency)})
