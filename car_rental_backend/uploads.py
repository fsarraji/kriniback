"""Chemins de stockage partagés.

Chaque agence possède un dossier à la racine « media » nommé avec son nom
(ex : `media/rental-casablanca/vehicles_images/...`) contenant des
sous-dossiers : `vehicles_images`, `clients_documents/cin|permis`,
`agency_logos`, `agency_cachets`.
"""

import os
import re

FALLBACK_SEGMENT = 'divers'


def safe_segment(value, fallback=FALLBACK_SEGMENT):
    s = re.sub(r'[^A-Za-z0-9]+', '-', str(value or ''))
    s = s.strip('-').lower()
    return s or fallback


def agency_segment(instance):
    """Retourne le dossier de l'agence pour une instance quelconque.

    Pour les modèles liés à une agence (Vehicle, Client, Expense) on lit
    `instance.agency`. Pour PdfJob on passe par `instance.contract.agency`.
    Pour le modèle Agency lui-même, le dossier porte son propre nom.
    """
    agency = getattr(instance, 'agency', None)
    if agency is None and hasattr(instance, 'nom_agence'):
        agency = instance
    if agency is None:
        contract = getattr(instance, 'contract', None)
        agency = getattr(contract, 'agency', None) if contract is not None else None
    name = getattr(agency, 'nom_agence', None) or FALLBACK_SEGMENT
    return safe_segment(name)


def _safe_filename(filename):
    base = os.path.splitext(os.path.basename(str(filename)))[0]
    ext = os.path.splitext(os.path.basename(str(filename)))[1].lower()
    return f'{safe_segment(base, fallback="document")}{ext}'


def vehicle_image_upload_to(instance, filename):
    marque = safe_segment(instance.marque.name) if instance.marque_id else 'x'
    modele = safe_segment(instance.modele.name) if instance.modele_id else 'x'
    matricule = safe_segment(instance.matricule)
    ext = os.path.splitext(filename)[1].lower()
    name = f'{marque}-{modele}-{matricule}{ext}'
    return f'{agency_segment(instance)}/vehicles_images/{name}'


def _client_document_path(instance, kind, filename):
    name = _safe_filename(filename)
    return f'{agency_segment(instance)}/clients_documents/{kind}/{name}'


def client_document_upload_cin(instance, filename):
    """Upload des scans CIN/passeport : `<agence>/clients_documents/cin/`."""
    return _client_document_path(instance, 'cin', filename)


def client_document_upload_permis(instance, filename):
    """Upload des scans permis : `<agence>/clients_documents/permis/`."""
    return _client_document_path(instance, 'permis', filename)


def client_photo_upload(instance, filename):
    """Upload de la photo de profil : `<agence>/clients_photos/`."""
    return f'{agency_segment(instance)}/clients_photos/{_safe_filename(filename)}'


def agency_logo_upload_to(instance, filename):
    name = _safe_filename(filename)
    return f'{agency_segment(instance)}/agency_logos/{name}'


def agency_cachet_upload_to(instance, filename):
    name = _safe_filename(filename)
    return f'{agency_segment(instance)}/agency_cachets/{name}'


def expense_receipt_upload_to(instance, filename):
    name = _safe_filename(filename)
    return f'{agency_segment(instance)}/expenses_receipts/{name}'


def pdf_job_upload_to(instance, filename):
    name = _safe_filename(filename)
    return f'{agency_segment(instance)}/pdf_jobs/{name}'
