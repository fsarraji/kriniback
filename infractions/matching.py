"""Rapprochement infraction -> véhicule -> contrat -> client."""

import re

from django.db.models import Q

from contracts.models import Contract
from fleet.models import Vehicle


def normalize_plate(value: str) -> str:
    """'12345 - A - 6' / '12345-أ-6' -> '12345A6' (comparaison tolérante)."""
    return re.sub(r"[\s\-_/.]", "", value or "").upper()


def find_vehicle(agency, matricule):
    target = normalize_plate(matricule)
    if not target:
        return None
    for v in Vehicle.objects.filter(agency=agency):
        if target in (
            normalize_plate(v.matricule),
            normalize_plate(getattr(v, "matricule_definitif", "") or ""),
        ):
            return v
    return None


def contracts_covering(vehicle, when):
    """Contrats non annulés dont la période couvre `when`."""
    return list(
        Contract.objects.filter(vehicle=vehicle)
        .exclude(statut__in=["ANNULE", "RESERVE"])
        .filter(date_sortie__lte=when)
        .filter(
            Q(date_retour_effective__gte=when)
            | Q(date_retour_effective__isnull=True, date_retour_prevue__gte=when)
            | Q(date_retour_effective__isnull=True, date_retour_prevue__isnull=True)
        )
    )


def match_infraction(infraction):
    """Remplit vehicle/contract/client et le statut. Ne touche pas aux infractions déjà envoyées."""
    if infraction.status in (infraction.SUBMITTED, infraction.QUEUED):
        return infraction

    vehicle = find_vehicle(infraction.agency, infraction.matricule)
    infraction.vehicle = vehicle
    infraction.contract = None
    infraction.client = None

    if vehicle is None:
        infraction.status = infraction.NO_CONTRACT
        infraction.error = "Véhicule introuvable pour cette immatriculation."
    else:
        found = contracts_covering(vehicle, infraction.date_infraction)
        if len(found) == 1:
            infraction.contract = found[0]
            infraction.client = found[0].client
            infraction.status = infraction.MATCHED
            infraction.error = ""
        else:
            infraction.status = infraction.NO_CONTRACT
            infraction.error = (
                "Aucun contrat couvrant cette date."
                if not found
                else f"{len(found)} contrats couvrent cette date : choisir manuellement."
            )
    infraction.save()
    return infraction
