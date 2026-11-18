"""Textes des alertes (Notification) par code et par langue.

Chaque alerte est créée avec un `code` et des `params` ; le texte est rendu à la demande dans
la langue du lecteur (en-tête Accept-Language). Le français est aussi stocké dans
`title`/`message` (e-mails, anciennes lignes sans code, repli).
"""

# code -> langue -> (titre, message). Les variables {nom} viennent de `params`.
TEMPLATES = {
    "INSURANCE_EXPIRED": {
        "fr": ("Assurance expirée - {plate}", "Assurance du véhicule {plate} expirée depuis {days} jour(s)."),
        "ar": ("انتهاء التأمين - {plate}", "انتهى تأمين السيارة {plate} منذ {days} يوم."),
    },
    "INSURANCE_SOON": {
        "fr": ("Assurance bientôt expirée - {plate}", "Assurance du véhicule {plate} expire dans {days} jour(s) ({date})."),
        "ar": ("اقتراب انتهاء التأمين - {plate}", "ينتهي تأمين السيارة {plate} خلال {days} يوم ({date})."),
    },
    "VISITE_EXPIRED": {
        "fr": ("Visite technique expirée - {plate}", "Visite technique du véhicule {plate} expirée depuis {days} jour(s)."),
        "ar": ("انتهاء الفحص التقني - {plate}", "انتهى الفحص التقني للسيارة {plate} منذ {days} يوم."),
    },
    "VISITE_SOON": {
        "fr": ("Visite technique bientôt expirée - {plate}", "Visite technique du véhicule {plate} expire dans {days} jour(s) ({date})."),
        "ar": ("اقتراب انتهاء الفحص التقني - {plate}", "ينتهي الفحص التقني للسيارة {plate} خلال {days} يوم ({date})."),
    },
    "VIDANGE": {
        "fr": ("Vidange à prévoir - {plate}", "{plate} : {km} km, vidange prévue à {next_km} km."),
        "ar": ("موعد تغيير الزيت - {plate}", "{plate} : {km} كم، تغيير الزيت مقرّر عند {next_km} كم."),
    },
    "RETURN_OVERDUE": {
        "fr": ("Retour en retard - {plate}", "Contrat #{contract} ({client}) : retour prévu le {when}."),
        "ar": ("إرجاع متأخر - {plate}", "العقد #{contract} ({client}) : الإرجاع كان مقرّراً في {when}."),
    },
    "RETURN_DUE": {
        "fr": ("Retour prévu bientôt - {plate}", "Contrat #{contract} ({client}) : retour le {when}."),
        "ar": ("إرجاع مرتقب قريباً - {plate}", "العقد #{contract} ({client}) : الإرجاع في {when}."),
    },
    "GEOFENCE": {
        "fr": ("Hors zone autorisée - {plate}", "Contrat #{contract} ({client}) : le véhicule {plate} est à {km} km de la zone « {zone} »."),
        "ar": ("خارج المنطقة المسموحة - {plate}", "العقد #{contract} ({client}) : السيارة {plate} على بعد {km} كم من المنطقة «{zone}»."),
    },
}


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render_alert(code, params, lang="fr"):
    """(titre, message) dans `lang` (repli français). None si le code est inconnu."""
    entry = TEMPLATES.get(code)
    if not entry:
        return None
    title, message = entry.get(lang) or entry["fr"]
    values = _Safe(params or {})
    return title.format_map(values), message.format_map(values)


def lang_from_request(request):
    """'ar' si l'en-tête Accept-Language commence par ar, sinon 'fr'."""
    header = (request.META.get("HTTP_ACCEPT_LANGUAGE", "") if request else "").strip().lower()
    return "ar" if header.startswith("ar") else "fr"
