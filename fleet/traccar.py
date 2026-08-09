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
        logger.warning("Erreur Traccar %s %s: %s", method, url, exc)
        raise TraccarError(f"Impossible de joindre le serveur Traccar ({url}): {exc}") from exc
    try:
        return resp.json()
    except ValueError as exc:
        raise TraccarError("Réponse Traccar invalide (JSON attendu).") from exc


def get_devices(agency=None):
    """Liste tous les dispositifs Traccar accessibles. [{id, name, uniqueId, status, ...}]"""
    return _request("GET", "/devices", agency=agency) or []


def get_positions(agency=None):
    """Dernières positions connues de tous les dispositifs. [{deviceId, latitude, longitude, speed, ...}]"""
    return _request("GET", "/positions", agency=agency) or []


def get_device_position(device_id, agency=None):
    """Dernière position connue d'un dispositif (ou None si inconnue)."""
    positions = _request("GET", f"/positions/device/{device_id}", agency=agency) or []
    return positions[-1] if positions else None


def get_route(device_id, from_iso, to_iso, agency=None):
    """Historique de route d'un dispositif entre deux dates ISO. [position, ...]"""
    return _request(
        "GET",
        "/reports/route",
        params={"deviceId": device_id, "from": from_iso, "to": to_iso},
        agency=agency,
    ) or []


def positions_by_device(agency=None):
    """Dictionnaire {device_id: position} à partir de la liste des dernières positions."""
    return {p.get("deviceId"): p for p in get_positions(agency=agency) if p.get("deviceId")}


def normalize_position(position):
    """Transforme une position brute Traccar en objet propre pour l'API Krini."""
    if not position:
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
