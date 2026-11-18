"""Tâches périodiques : alertes (assurance, visite, vidange, retours) puis PDF en attente.

À planifier toutes les heures (Render Cron Job, cron système ou GitHub Actions) :

    python manage.py run_periodic

Chaque étape est isolée : l'échec d'une tâche n'empêche pas les suivantes, et le
code de sortie est non nul si une étape a échoué (pour alerter l'outil de cron).
"""
import logging

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Lance les tâches périodiques (alertes puis traitement des PDF en attente)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-email", action="store_true", help="Ne pas envoyer d'e-mails d'alerte"
        )

    def handle(self, *args, **opts):
        failures = []

        steps = [
            ("send_alerts", {"no_email": opts["no_email"]}),
            ("process_pdf_jobs", {}),
        ]
        for name, kwargs in steps:
            try:
                call_command(name, **kwargs)
                self.stdout.write(self.style.SUCCESS(f"{name}: OK"))
            except Exception:
                logger.exception("periodic_task_failed", extra={"task": name})
                self.stderr.write(self.style.ERROR(f"{name}: ÉCHEC"))
                failures.append(name)

        if failures:
            raise CommandError(f"Tâches en échec : {', '.join(failures)}")
