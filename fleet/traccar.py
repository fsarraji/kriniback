"""Client de l'API Traccar (suivi GPS des véhicules).

Le backend Django joue le rôle de proxy : il expose des endpoints internes
(/api/gps/...) et appelle Traccar avec les identifiants du compte de l'agence.

Chaque agence possède son propre compte serveur Traccar (URL + utilisateur +
mot de passe). Les fonctions acceptent donc l'objet `agency` en paramètre ;
si l'agence n'a pas renseigné sa configuration, on retombe sur les variables
d'environnement globales (TRACCAR_URL, TRACCAR_USER, TRACCAR_PASSWORD).
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Traccar stocke la vitesse en nœuds. 1 nœud = 1.852 km/h.
KNOTS_TO_KPH = 1.852


class TraccarError(Exception):
    """Erreur de communication avec le serveur Traccar."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class TraccarNotConfigured(Exception):
    """Traccar n'est pas configuré (pas de compte d'agence ni de config globale)."""


def _credentials(agency=None):
    """Retourne (url, username, password) pour l'agence ou la config globale."""
    if agency is not None:
        url = (agency.traccar_url or "").strip()
        user = (agency.traccar_username or "").strip()
        password = agency.traccar_password or ""
        if url and user:
            return url, user, password
    url = (settings.TRACCAR_URL or "").strip()
    user = (settings.TRACCAR_USER or "").strip()
    password = settings.TRACCAR_PASSWORD or ""
    return url, user, password


def is_configured(agency=None):
    url, user, password = _credentials(agency)
    return bool(url and user and password)


def _auth(agency=None):
    _, user, password = _credentials(agency)
    return (user, password)


def _request(method, path, params=None, json=None, timeout=10, agency=None):
    if not is_configured(agency):
        raise TraccarNotConfigured(
            "Traccar n'est pas configuré pour cette agence."
        )
    url, _, _ = _credentials(agency)
    url = f"{url}/api{path}"
    try:
        resp = requests.request(
            method,
            url,
            params=params,
            json=json,
            auth=_auth(agency),
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        logger.warning("Erreur Traccar %s %s: %s", method, url, exc)
        raise TraccarError(
            f"Impossible de joindre le serveur Traccar ({url}): {exc}",
            status_code=status_code,
        ) from exc
    try:
        return resp.json()
    except ValueError as exc:
        if resp.status_code in (200, 201, 202, 204) and not resp.text.strip():
            return None
        raise TraccarError("Réponse Traccar invalide (JSON attendu).") from exc


def get_devices(agency=None):
    """Liste tous les dispositifs Traccar accessibles. [{id, name, uniqueId, status, ...}]"""
    return _request("GET", "/devices", agency=agency) or []


def get_positions(agency=None):
    """Dernières positions connues de tous les dispositifs. [{deviceId, latitude, longitude, speed, ...}]"""
    return _request("GET", "/positions", agency=agency) or []


def get_device_position(device_id, agency=None):
    """Dernière position connue d'un dispositif (ou None si inconnue).

    Utilise GET /api/positions?deviceId=... (l'ancien endpoint
    /api/positions/device/{id} est absent sur les versions récentes de Traccar).
    """
    positions = _request("GET", "/positions", params={"deviceId": device_id}, agency=agency) or []
    return positions[-1] if positions else None


def create_device(name, unique_id, agency=None):
    """Crée un dispositif sur le serveur Traccar et retourne l'objet créé.

    Retourne un dict {id, name, uniqueId, status, ...}. Le champ uniqueId
    (IMEI du périphérique GPS) doit être unique côté Traccar.
    """
    return _request("POST", "/devices", json={"name": name, "uniqueId": unique_id}, agency=agency)


def update_device(device_id, name=None, unique_id=None, agency=None):
    """Met à jour un dispositif Traccar (name / uniqueId)."""
    payload = {}
    if name is not None:
        payload["name"] = name
    if unique_id is not None:
        payload["uniqueId"] = unique_id
    return _request("PUT", f"/devices/{device_id}", json=payload, agency=agency)


def delete_device(device_id, agency=None):
    """Supprime un dispositif du serveur Traccar (réponse 204, retourne None)."""
    return _request("DELETE", f"/devices/{device_id}", agency=agency)


def update_device_accumulators(device_id, total_distance_m=None, hours=None, agency=None):
    """Corrige le total kilométrique (odomètre) et/ou les heures moteur d'un dispositif.

    Utilise l'endpoint officiel Traccar PUT /api/devices/{id}/accumulators qui met à
    jour la dernière position du dispositif (le champ totalDistance est en mètres,
    hours en heures). Retourne None (réponse 204) ou lève TraccarError.
    """
    payload = {"deviceId": device_id}
    if total_distance_m is not None:
        payload["totalDistance"] = total_distance_m
    if hours is not None:
        payload["hours"] = hours
    return _request("PUT", f"/devices/{device_id}/accumulators", json=payload, agency=agency)


def get_route(device_id, from_iso, to_iso, agency=None):
    """Historique de route d'un dispositif entre deux dates ISO. [position, ...]"""
    return _request(
        "GET",
        "/reports/route",
        params={"deviceId": device_id, "from": from_iso, "to": to_iso},
        agency=agency,
    ) or []


def get_command_types(device_id, agency=None):
    """Commandes supportées par le protocole du dispositif. Ex: [{type: 'engineStop'}, ...]"""
    return _request("GET", "/commands/types", params={"deviceId": device_id}, agency=agency) or []


def send_command(device_id, command_type, attributes=None, agency=None):
    """Envoie immédiatement une commande au dispositif (ex: engineStop, engineResume, custom).

    Retourne la commande créée côté Traccar {id, deviceId, type, attributes, ...}.
    """
    payload = {"deviceId": device_id, "type": command_type}
    if attributes:
        payload["attributes"] = attributes
    return _request("POST", "/commands/send", json=payload, agency=agency)


def positions_by_device(agency=None):
    """Dictionnaire {device_id: position} à partir de la liste des dernières positions."""
    return {p.get("deviceId"): p for p in get_positions(agency=agency) if p.get("deviceId")}


def _position_has_fix(position):
    """Vrai si la position Traccar contient des coordonnées réellement exploitables.

    De nombreux traceurs (GT06, ...) envoient des paquets de statut avec
    valid=False (pas de nouveau fix GPS dans ce paquet) mais en réutilisant les
    dernières coordonnées GPS connues : le dispositif est bien « online » côté
    Traccar et sa position affichée est la dernière position réelle. On ne filtre
    donc que les positions réellement inexploitables : coordonnées absentes/nulles
    (le véhicule apparaîtrait à 0,0, au milieu du golfe de Guinée) ou fixTime
    antérieur à 2000 (traceurs qui remontent l'époque).
    """
    if not position:
        return False
    lat = position.get("latitude")
    lon = position.get("longitude")
    if lat in (None, 0) or lon in (None, 0):
        return False
    fix_time = position.get("fixTime")
    if isinstance(fix_time, str) and len(fix_time) >= 4:
        try:
            if int(fix_time[:4]) < 2000:
                return False
        except ValueError:
            pass
    return True


def normalize_position(position):
    """Transforme une position brute Traccar en objet propre pour l'API Krini.

    Retourne None si la position est absente ou sans fix GPS exploitable.
    """
    if not _position_has_fix(position):
        return None
    attributes = position.get("attributes") or {}
    return {
        "deviceId": position.get("deviceId"),
        "latitude": position.get("latitude"),
        "longitude": position.get("longitude"),
        "altitude": position.get("altitude"),
        "speed_kph": round((position.get("speed") or 0) * KNOTS_TO_KPH, 1),
        "course": position.get("course"),
        "fixTime": position.get("fixTime"),
        "valid": position.get("valid", True),
        "address": position.get("address"),
        "ignition": attributes.get("ignition"),
        "moving": attributes.get("moving"),
        "fuel_level": attributes.get("fuelLevel") or attributes.get("fuel"),
        "odometer": attributes.get("odometer") or attributes.get("totalDistance"),
        "battery": attributes.get("batteryLevel") or attributes.get("battery"),
    }
