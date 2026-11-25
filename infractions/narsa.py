"""Règles propres au portail NARSA : format de plaque et données obligatoires du conducteur."""

import re

# Lettres des plaques marocaines : latin (saisie courante) -> arabe (format NARSA).
LATIN_TO_ARABIC = {
    "A": "أ",
    "B": "ب",
    "J": "ج",
    "D": "د",
    "H": "ه",
    "W": "و",
    "T": "ط",
}
ARABIC_LETTERS = set(LATIN_TO_ARABIC.values())
MIN_ADDRESS_LENGTH = 5


def plate_parts(value):
    """'12345-A-6' / '12345 أ 6' -> ('12345', 'أ', '6'), ou None si le format n'est pas reconnu."""
    m = re.fullmatch(
        r"\s*(\d{1,6})\s*[-\s/]*\s*([A-Za-zء-ي])\s*[-\s/]*\s*(\d{1,2})\s*", value or ""
    )
    if not m:
        return None
    num, letter, serie = m.groups()
    letter = LATIN_TO_ARABIC.get(letter.upper(), letter)
    return num, letter, serie


def narsa_plate(value):
    """Plaque au format affiché par NARSA : '37013 - أ - 49' (None si non reconnue, ex. WW)."""
    parts = plate_parts(value)
    return " - ".join(parts) if parts else None


def narsa_missing_fields(client):
    """Champs obligatoires du formulaire NARSA (personne physique) absents chez ce client."""
    if client is None:
        return ["client"]
    checks = {
        "nom": client.nom,
        "prenom": client.prenom,
        "nom_ar": client.nom_ar,
        "prenom_ar": client.prenom_ar,
        "type_document": client.type_document,
        "cin_passport": client.cin_passport,
        "permis_conduite": client.permis_conduite,
        "adresse": client.adresse,
        "adresse_ar": client.adresse_ar,
        "localite_narsa": client.localite_narsa,
    }
    missing = [k for k, v in checks.items() if not (v or "").strip()]
    # NARSA rejette une adresse de moins de 5 caractères (constaté sur le formulaire)
    for key in ("adresse", "adresse_ar"):
        value = (checks[key] or "").strip()
        if value and len(value) < MIN_ADDRESS_LENGTH and key not in missing:
            missing.append(key)
    return missing
