"""Règles de disponibilité des véhicules.

Délai de confort : une voiture est considérée indisponible non seulement
pendant la période de location, mais aussi quelques heures avant (préparation,
nettoyage) et après (retour, contrôle). Ce délai évite les enchaînements trop
serrés entre une réservation/contrat et un autre.

Exemple : une voiture réservée le 9/09 à 20h ne peut pas être louée jusqu'au
9/09 à 18h si le délai de confort est >= 2h (18h + 2h = 20h touche la prise).
"""

from datetime import timedelta

# Délai de confort par défaut appliqué avant le début et après la fin de
# chaque location. Configurable par agence (champ Agency.location_buffer_hours).
LOCATION_BUFFER_HOURS = 2
LOCATION_BUFFER = timedelta(hours=LOCATION_BUFFER_HOURS)


def agency_buffer(agency):
    """Délai de confort de l'agence, ou la valeur par défaut si absente."""
    if agency is None:
        return LOCATION_BUFFER
    hours = getattr(agency, 'location_buffer_hours', None)
    try:
        return timedelta(hours=float(hours))
    except (TypeError, ValueError):
        return LOCATION_BUFFER


def max_buffer(agencies=None):
    """Délai de confort maximal parmi les agences données (ou toutes les
    agences actives si aucune n'est fournie). Utilisé pour les requêtes
    inter-agences (ex. catalogue public) afin de ne sous-estimer aucun délai."""
    from agency.models import Agency
    qs = agencies if agencies is not None else Agency.objects.filter(is_active=True)
    values = [
        getattr(a, 'location_buffer_hours', None)
        for a in qs.only('location_buffer_hours')
    ]
    hours = [float(v) for v in values if v is not None]
    if not hours:
        return LOCATION_BUFFER
    return timedelta(hours=max(hours))


def overlaps(period_start, period_end, existing_start, existing_end, buffer=LOCATION_BUFFER):
    """Vrai si la fenêtre demandée [period_start, period_end] (étendue du délai
    de confort) chevauche une location existante [existing_start, existing_end]."""
    if period_start is None or period_end is None:
        return False
    if existing_start is None or existing_end is None:
        return False
    # Fenêtre effectivement occupée incluant le délai de confort.
    start = period_start - buffer
    end = period_end + buffer
    return existing_start < end and start < existing_end


def extend(period_start, period_end, buffer=LOCATION_BUFFER):
    """Étend une période demandée du délai de confort (bornes incluses)."""
    return period_start - buffer, period_end + buffer
