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
    create_device,
    delete_device,
    get_command_types,
    get_device_position,
    get_devices,
    get_route,
    normalize_position,
    positions_by_device,
    send_command,
    update_device_accumulators,
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


def _vehicle_payload(vehicle, device, position):
    data = VehicleSerializer(vehicle).data
    data['position'] = normalize_position(position)
    data['gps_status'] = None
    data['gps_last_update'] = None
    if device:
        status = device.get('status')
        data['gps_status'] = status if status in ('online', 'offline') else None
        data['gps_last_update'] = device.get('lastUpdate')
    return data


def _sync_kilometrage(vehicle, position):
    """Synchronise Vehicle.kilometrage depuis la position Traccar.

    Traccar est la source de vérité du kilométrage : dès que la valeur entière
    (km) de l'odomètre du dernier point change, on l'écrit dans Krini. Aucune
    écriture si la valeur n'a pas franchi un seuil de 1 km.
    """
    normalized = normalize_position(position)
    odometer = normalized.get('odometer') if normalized else None
    # On ne remonte jamais un kilométrage nul : un totalDistance à 0 (traceur
    # neuf ou position sans compteur) ne doit pas écraser le kilométrage réel.
    if odometer is None or odometer <= 0:
        return
    new_km = int(round(odometer / 1000.0))
    if new_km != vehicle.kilometrage:
        vehicle.kilometrage = new_km
        vehicle.save(update_fields=['kilometrage'])


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
            device = devices_by_id.get(device_id) if device_id else None
            position = positions.get(device_id) if device_id else None
            _sync_kilometrage(vehicle, position)
            results.append(_vehicle_payload(vehicle, device, position))

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
        device = devices_by_id.get(device_id) if device_id else None
        position = positions.get(device_id) if device_id else None
        _sync_kilometrage(vehicle, position)
        return Response(_vehicle_payload(vehicle, device, position))


class GpsDevicesView(APIView):
    """Dispositifs Traccar : liste (GET), création (POST) et suppression (DELETE)."""

    permission_classes = [permissions.IsAuthenticated]

    def _devices_with_vehicles(self, request):
        """Dispositifs Traccar enrichis du véhicule auquel chacun est associé."""
        devices = get_devices(agency=request.user.agency)
        linked = _scoped_vehicles(request).filter(traccar_device_id__isnull=False)
        by_device = {v.traccar_device_id: v for v in linked}
        return [
            {
                **d,
                "vehicle_id": by_device.get(d.get("id")).id if by_device.get(d.get("id")) else None,
                "vehicle_matricule": by_device.get(d.get("id")).matricule if by_device.get(d.get("id")) else None,
            }
            for d in devices
        ]

    def get(self, request):
        try:
            devices = self._devices_with_vehicles(request)
        except TraccarNotConfigured:
            return Response({"tracking": False, "devices": []})
        except TraccarError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response({"tracking": True, "devices": devices})

    def post(self, request):
        unique_id = (request.data.get('uniqueId') or request.data.get('unique_id') or '').strip()
        if not unique_id:
            return Response({"detail": "Le champ uniqueId (IMEI du dispositif) est requis."}, status=400)
        name = (request.data.get('name') or '').strip() or f'Dispositif {unique_id}'
        agency = request.user.agency
        try:
            device = create_device(name, unique_id, agency=agency)
        except TraccarNotConfigured:
            return Response(
                {"detail": "Traccar n'est pas configuré pour votre agence. Renseignez l'URL et le compte Traccar dans les Paramètres."},
                status=503,
            )
        except TraccarError as exc:
            # uniqueId peut déjà exister côté Traccar : on le retrouve et on le renvoie
            try:
                device = next(
                    (d for d in get_devices(agency=agency) if str(d.get('uniqueId', '')).strip() == unique_id),
                    None,
                )
            except TraccarError as exc2:
                return Response({"detail": f"Impossible de joindre le serveur Traccar ({exc2})."}, status=502)
            except TraccarNotConfigured:
                device = None
            if not device:
                if exc.status_code == 400:
                    return Response(
                        {
                            "detail": (
                                f"Le serveur Traccar a refusé la création (erreur 400). "
                                f"L'ID/IMEI « {unique_id} » est déjà enregistré sur le serveur Traccar, "
                                f"mais il n'est pas accessible avec le compte de votre agence. "
                                f"Utilisez un autre IMEI ou vérifiez le compte Traccar."
                            )
                        },
                        status=502,
                    )
                return Response(
                    {"detail": f"Impossible de créer le dispositif sur le serveur Traccar ({exc})."},
                    status=502,
                )
        return Response(device)

    def delete(self, request):
        """Supprime un dispositif Traccar et le dissocie de tout véhicule.

        Paramètre : ?device_id=<id>
        """
        device_id = request.query_params.get('device_id')
        if not device_id:
            return Response({"detail": "Paramètre device_id requis."}, status=400)
        try:
            device_id = int(device_id)
        except (TypeError, ValueError):
            return Response({"detail": "device_id doit être un entier."}, status=400)

        try:
            delete_device(device_id, agency=request.user.agency)
        except TraccarNotConfigured:
            return Response({"detail": "Traccar n'est pas configuré pour votre agence."}, status=503)
        except TraccarError as exc:
            return Response({"detail": str(exc)}, status=502)

        _scoped_vehicles(request).filter(traccar_device_id=device_id).update(traccar_device_id=None)
        return Response({"status": "deleted", "device_id": device_id})


class GpsDeviceAssociateView(APIView):
    """Association / dissociation d'un dispositif Traccar à un véhicule.

    - POST {"device_id": 30, "vehicle_id": 7}   → associer (délie tout autre véhicule)
    - POST {"device_id": 30}                     → dissocier
    - POST {"dissociate_all": true}              → dissocier tous les dispositifs des véhicules
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        data = request.data or {}
        if str(data.get('dissociate_all', '')).lower() in ('true', '1', 'yes', 'on'):
            count = _scoped_vehicles(request).filter(traccar_device_id__isnull=False).update(traccar_device_id=None)
            return Response({"status": "dissociated_all", "vehicles": count})
        try:
            device_id = int(data.get('device_id'))
        except (TypeError, ValueError):
            return Response({"detail": "Le champ device_id est requis et doit être un entier."}, status=400)

        vehicle = None
        vehicle_id = data.get('vehicle_id')
        if vehicle_id not in (None, ''):
            try:
                vehicle_id = int(vehicle_id)
            except (TypeError, ValueError):
                return Response({"detail": "vehicle_id doit être un entier."}, status=400)
            vehicle = _scoped_vehicles(request).filter(pk=vehicle_id).first()
            if not vehicle:
                return Response({"detail": "Véhicule introuvable."}, status=404)

        # Le dispositif doit exister sur le serveur Traccar du compte de l'agence.
        try:
            device = next(
                (d for d in get_devices(agency=request.user.agency) if d.get('id') == device_id),
                None,
            )
        except TraccarNotConfigured:
            return Response({"detail": "Traccar n'est pas configuré pour votre agence."}, status=503)
        except TraccarError as exc:
            return Response({"detail": str(exc)}, status=502)
        if not device:
            return Response({"detail": "Dispositif introuvable sur le serveur Traccar."}, status=404)

        # Un dispositif ne peut être lié qu'à un seul véhicule : on le délie des autres.
        qs = _scoped_vehicles(request).filter(traccar_device_id=device_id)
        if vehicle:
            qs = qs.exclude(pk=vehicle.pk)
        qs.update(traccar_device_id=None)

        if vehicle:
            vehicle.traccar_device_id = device_id
            imei = (device.get('uniqueId') or '').strip()
            if imei:
                vehicle.gps_imei = imei
            vehicle.save(update_fields=['traccar_device_id', 'gps_imei'])
            return Response({"status": "associated", "device_id": device_id, "vehicle_id": vehicle.id})

        return Response({"status": "dissociated", "device_id": device_id})


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


class GpsSetOdometerView(APIView):
    """POST /api/gps/odometer/ — Corrige le kilométrage (odomètre) d'un véhicule sur Traccar.

    Corps JSON attendu :
        {"vehicle_id": 7, "km": 45200, "update_krini": false}
        {"device_id": 123, "km": 45200}   (dispositif lié à un véhicule de l'agence)

    Le champ totalDistance de Traccar est exprimé en mètres ; km est converti
    automatiquement. Met à jour la dernière position du dispositif.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        data = request.data or {}
        km = data.get('km')
        vehicle_id = data.get('vehicle_id')
        device_id = data.get('device_id')
        update_krini = str(data.get('update_krini', '')).lower() in ['true', '1', 'yes', 'on']
        hours = data.get('hours')

        try:
            km = float(km)
        except (TypeError, ValueError):
            return Response({"detail": "Le champ km (kilométrage) est requis et doit être un nombre."}, status=400)
        if km < 0:
            return Response({"detail": "Le kilométrage doit être positif."}, status=400)

        if vehicle_id:
            vehicle = _scoped_vehicles(request).filter(pk=vehicle_id).first()
            if not vehicle:
                return Response({"detail": "Véhicule introuvable."}, status=404)
            device_id = vehicle.traccar_device_id
            if not device_id:
                return Response({"detail": "Ce véhicule n'a pas de dispositif Traccar associé."}, status=400)
        elif device_id:
            device_id = int(device_id)
            vehicle = _scoped_vehicles(request).filter(traccar_device_id=device_id).first()
            if not vehicle:
                return Response(
                    {"detail": "Ce dispositif Traccar n'est lié à aucun véhicule de votre agence."},
                    status=403,
                )
        else:
            return Response({"detail": "Précisez vehicle_id ou device_id."}, status=400)

        try:
            before = normalize_position(get_device_position(device_id, agency=request.user.agency))
        except TraccarNotConfigured:
            return Response({"detail": "Traccar n'est pas configuré pour votre agence."}, status=503)
        except TraccarError as exc:
            return Response({"detail": str(exc)}, status=502)

        try:
            update_device_accumulators(
                device_id,
                total_distance_m=km * 1000,
                hours=hours,
                agency=request.user.agency,
            )
        except TraccarNotConfigured:
            return Response({"detail": "Traccar n'est pas configuré pour votre agence."}, status=503)
        except TraccarError as exc:
            return Response({"detail": str(exc)}, status=502)

        try:
            after = normalize_position(get_device_position(device_id, agency=request.user.agency))
        except (TraccarNotConfigured, TraccarError):
            after = None

        if update_krini:
            vehicle.kilometrage = int(round(km))
            vehicle.save(update_fields=['kilometrage'])

        return Response({
            "vehicle_id": vehicle.id,
            "device_id": device_id,
            "km": km,
            "before": before,
            "position": after,
            "update_krini": update_krini,
            "kilometrage_krini": vehicle.kilometrage if update_krini else None,
        })


class GpsCommandsView(APIView):
    """Commandes Traccar : liste des commandes supportées (GET) et envoi (POST).

    - GET  /api/gps/commands/?vehicle_id=X   → commandes supportées par le dispositif
    - POST /api/gps/commands/                → {vehicle_id, type, attributes?}
    """
    permission_classes = [permissions.IsAuthenticated]

    def _vehicle(self, request, vehicle_id):
        vehicle = _scoped_vehicles(request).filter(pk=vehicle_id).first()
        if not vehicle:
            return None, Response({"detail": "Véhicule introuvable."}, status=404)
        if not vehicle.traccar_device_id:
            return None, Response({"detail": "Ce véhicule n'a pas de dispositif Traccar associé."}, status=400)
        return vehicle, None

    def get(self, request):
        vehicle_id = request.query_params.get('vehicle_id')
        if not vehicle_id:
            return Response({"detail": "Paramètre vehicle_id requis."}, status=400)
        vehicle, err = self._vehicle(request, vehicle_id)
        if err:
            return err
        try:
            types = get_command_types(vehicle.traccar_device_id, agency=request.user.agency)
        except TraccarNotConfigured:
            return Response({"detail": "Traccar n'est pas configuré pour votre agence."}, status=503)
        except TraccarError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response({
            "vehicle_id": vehicle.id,
            "device_id": vehicle.traccar_device_id,
            "commands": [t.get("type") for t in types if t.get("type")],
        })

    def post(self, request):
        vehicle_id = request.data.get('vehicle_id')
        command_type = (request.data.get('type') or '').strip()
        attributes = request.data.get('attributes') or {}
        if not isinstance(attributes, dict):
            return Response({"detail": "Le champ attributes doit être un objet JSON."}, status=400)
        if not vehicle_id:
            return Response({"detail": "Le champ vehicle_id est requis."}, status=400)
        if not command_type:
            return Response({"detail": "Le champ type (commande Traccar) est requis."}, status=400)

        vehicle, err = self._vehicle(request, vehicle_id)
        if err:
            return err

        if command_type == 'custom' and not str(attributes.get('data') or '').strip():
            return Response({
                "detail": "La commande personnalisée nécessite le champ attributes.data "
                          "(les données hex à envoyer au boîtier)."
            }, status=400)

        try:
            supported = [
                t.get("type") for t in get_command_types(vehicle.traccar_device_id, agency=request.user.agency) if t.get("type")
            ]
            if supported and command_type not in supported:
                return Response({
                    "detail": f"Commande « {command_type} » non supportée par ce dispositif. "
                              f"Commandes disponibles : {', '.join(sorted(supported))}."
                }, status=400)
        except (TraccarNotConfigured, TraccarError):
            pass  # on tente l'envoi ; Traccar rejettera lui-même un type inconnu

        try:
            result = send_command(
                vehicle.traccar_device_id,
                command_type,
                attributes=attributes,
                agency=request.user.agency,
            )
        except TraccarNotConfigured:
            return Response({"detail": "Traccar n'est pas configuré pour votre agence."}, status=503)
        except TraccarError as exc:
            return Response({"detail": str(exc)}, status=502)

        return Response({"status": "sent", "device_id": vehicle.traccar_device_id, "command": result})
