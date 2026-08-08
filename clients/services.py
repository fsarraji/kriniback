import os
import threading
import traceback
import logging

from django.conf import settings
from django.core.mail import EmailMessage

from .models import Client

logger = logging.getLogger(__name__)


def _attachment_from_field(label, field):
    """Lit un champ image et le transforme en pièce jointe (nom, contenu, mimetype)."""
    if not field:
        return None
    try:
        field.open('rb')
        content = field.read()
        filename = os.path.basename(field.name) or f'{label}.jpg'
        mimetype = getattr(field.file, 'content_type', None) or 'application/octet-stream'
        return (filename, content, mimetype)
    except Exception:
        traceback.print_exc()
        return None
    finally:
        try:
            field.close()
        except Exception:
            pass


def _build_documents_email(client, agency, reservation=None):
    if not (agency and agency.email):
        return None

    attachments = [a for a in (
        _attachment_from_field('cin', client.scan_cin),
        _attachment_from_field('permis', client.scan_permis),
    ) if a]
    if not attachments:
        return None

    if reservation is not None:
        subject = f"Documents client — Réservation #{reservation.id}"
        body = (
            f"Bonjour,\n\n"
            f"Le client {client.prenom} {client.nom} a envoyé une demande de réservation "
            f"sur votre site (référence #{reservation.id}).\n\n"
            f"Coordonnées du client :\n"
            f"  Nom : {client.prenom} {client.nom}\n"
            f"  Téléphone : {client.telephone}\n"
            f"  Email : {client.email or '—'}\n"
            f"  CIN / Passeport : {client.cin_passport or '—'}\n"
            f"  Permis de conduite : {client.permis_conduite or '—'}\n\n"
            f"Véhicule réservé : {reservation.vehicle.marque} {reservation.vehicle.modele} "
            f"({reservation.vehicle.matricule})\n"
            f"Du : {reservation.date_sortie.strftime('%d/%m/%Y %H:%M')}\n"
            f"Au : {reservation.date_retour_prevue.strftime('%d/%m/%Y %H:%M')}\n\n"
            f"Les scans des documents du client sont joints à cet email.\n\n"
            f"Cordialement,\n"
            f"Le site de réservation Krini"
        )
    else:
        subject = f"Documents client — {client.prenom} {client.nom}"
        body = (
            f"Bonjour,\n\n"
            f"Le client {client.prenom} {client.nom} a fourni les scans de ses documents.\n\n"
            f"Coordonnées du client :\n"
            f"  Nom : {client.prenom} {client.nom}\n"
            f"  Téléphone : {client.telephone}\n"
            f"  Email : {client.email or '—'}\n"
            f"  CIN / Passeport : {client.cin_passport or '—'}\n"
            f"  Permis de conduite : {client.permis_conduite or '—'}\n\n"
            f"Les scans des documents sont joints à cet email.\n\n"
            f"Cordialement,\n"
            f"Le site de réservation Krini"
        )

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[agency.email],
    )
    for attachment in attachments:
        email.attach(*attachment)
    return email


def _send_in_background(email):
    try:
        email.send(fail_silently=False)
        logger.info('Email documents client envoyé à %s', email.to)
    except Exception:
        logger.exception('Échec de l\'envoi de l\'email documents client à %s (host=%s user=%s)',
                         email.to, settings.EMAIL_HOST, settings.EMAIL_HOST_USER)
        traceback.print_exc()


def send_client_documents_to_agency(client, agency, reservation=None):
    """Envoie par email les scans du client à l'agence (en arrière-plan)."""
    try:
        email = _build_documents_email(client, agency, reservation)
        if email is None:
            logger.warning('Email documents non envoyé pour client %s : agence %s email=%s, pièces jointes manquantes',
                           client.id, getattr(agency, 'nom_agence', '—'),
                           getattr(agency, 'email', None) if agency else None)
            return
        thread = threading.Thread(target=_send_in_background, args=(email,), daemon=True)
        thread.start()
    except Exception:
        traceback.print_exc()
