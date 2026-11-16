"""Génère les alertes (assurance, visite, vidange, retours) et envoie le récapitulatif.

À planifier une fois par jour (cron Render) :
    python manage.py send_alerts
"""
from django.core.management.base import BaseCommand

from agency.notifications import run_alerts


class Command(BaseCommand):
    help = (
        "Crée les notifications d'alerte et envoie un e-mail récapitulatif par agence."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-email", action="store_true", help="Ne pas envoyer d'e-mails"
        )

    def handle(self, *args, **opts):
        result = run_alerts(send_email=not opts["no_email"])
        total = sum(result.values())
        self.stdout.write(f"{total} nouvelle(s) alerte(s) pour {len(result)} agence(s)")
