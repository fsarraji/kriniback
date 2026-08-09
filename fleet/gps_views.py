"""Vues GPS : proxy entre l'API Krini et le serveur Traccar.

- GET /api/gps/positions/             → flotte avec dernière position Traccar
- GET /api/gps/positions/<pk>/        → un véhicule avec sa position
- GET /api/gps/devices/               → dispositifs Traccar (mapping véhicule ↔ dispositif)
- GET /api/gps/history/?vehicle_id=..&from=..&to=..  → historique de route
"""
from datetime import datetime

from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Vehicle
from .serializers import VehicleSerializer
from .traccar import (
    TraccarError,
    TraccarNotConfigured,
    get_device_position,
    get_devices,
    get_route,
    normalize_position,
    positions_by_device,
)


def _scoped_vehicles(request):
    base = Vehicle.objects.select_related('marque', 'modele', 'agency')
    if request.user.is_superuser:
        return base
    return base.filter(agency=request.user.agency)


def _resolve_device_id(vehicle, devices_by_name):
    """Trouve l'ID du dispositif Traccar du véhicule.

    Priorité : traccar_device_id explicite, sinon correspondance par
    matricule (name ou uniqueId du dispositif).
    """
    if vehicle.traccar_device_id:
        return vehicle.traccar_device_id
    if vehicle.matricule:
        key = vehicle.matricule.strip().lower()
        for dev_id, dev in devices_by_name.items():
            if key in (str(dev.get('name', '')).strip().lower(), str(dev.get('uniqueId', '')).strip().lower()):
                return dev_id
    return None


def _device_index(agency=None):
    """Construit les index des dispositifs et positions Traccar (compte de l'agence)."""
    devices = get_devices(agency=agency)
    devices_by_name = {d.get('id'): d for d in devices}
    positions = positions_by_device(agency=agency)
    return devices_by_name, positions


def _vehicle_payload(vehicle, device_id, position):
    data = VehicleSerializer(vehicle).data
    data['position'] = normalize_position(position)
    return data


class GpsPositionsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        vehicles = _scoped_vehicles(request)
        agency = request.user.agency
        try:
            devices_by_id, positions = _device_index(agency=agency)
        except TraccarNotConfigured:
            devices_by_id, positions = {}, {}
        except TraccarError:
            devices_by_id, positions = {}, {}

        results = []
        for vehicle in vehicles:
            device_id = _resolve_device_id(vehicle, devices_by_id)
            position = positions.get(device_id) if device_id else None
            results.append(_vehicle_payload(vehicle, device_id, position))

        return Response({
            "tracking": bool(devices_by_id),
            "vehicles": results,
        })


class GpsVehiclePositionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        vehicle = _scoped_vehicles(request).filter(pk=pk).first()
        if not vehicle:
            return Response({"detail": "Véhicule introuvable."}, status=404)
        try:
            devices_by_id, positions = _device_index(agency=request.user.agency)
        except (TraccarNotConfigured, TraccarError):
            devices_by_id, positions = {}, {}
        device_id = _resolve_device_id(vehicle, devices_by_id)
        position = positions.get(device_id) if device_id else None
        return Response(_vehicle_payload(vehicle, device_id, position))


class GpsDevicesView(APIView):
    """Liste les dispositifs Traccar pour permettre le mapping avec les véhicules."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            devices = get_devices(agency=request.user.agency)
        except TraccarNotConfigured:
            return Response({"tracking": False, "devices": []})
        except TraccarError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response({"tracking": True, "devices": devices})


class GpsHistoryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        vehicle_id = request.query_params.get('vehicle_id')
        from_iso = request.query_params.get('from')
        to_iso = request.query_params.get('to')
        if not vehicle_id:
            return Response({"detail": "Paramètre vehicle_id requis."}, status=400)
        if not from_iso or not to_iso:
            return Response({"detail": "Paramètres from et to requis (format ISO-8601)."}, status=400)

        vehicle = _scoped_vehicles(request).filter(pk=vehicle_id).first()
        if not vehicle:
            return Response({"detail": "Véhicule introuvable."}, status=404)
        if not vehicle.traccar_device_id:
            return Response({"detail": "Ce véhicule n'a pas de dispositif Traccar associé."}, status=400)

        try:
            from_ts = int(datetime.fromisoformat(from_iso).timestamp() * 1000)
            to_ts = int(datetime.fromisoformat(to_iso).timestamp() * 1000)
        except ValueError:
            return Response({"detail": "Dates invalides, utilisez le format ISO-8601 (ex: 2026-08-01T00:00:00)."}, status=400)

        try:
            route = get_route(vehicle.traccar_device_id, from_ts, to_ts, agency=request.user.agency)
        except TraccarNotConfigured:
            return Response({"detail": "Traccar n'est pas configuré côté serveur."}, status=503)
        except TraccarError as exc:
            return Response({"detail": str(exc)}, status=502)

        return Response({"vehicle_id": vehicle.id, "route": [normalize_position(p) for p in route]})
