"""Worker durable pour les PdfJob.

Traite les jobs PENDING et reprend les jobs PROCESSING bloqués (processus tué
en cours de route). À lancer en boucle (--loop) sur un worker Render, ou en cron.

    python manage.py process_pdf_jobs --loop --interval 5
"""
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from contracts.models import PdfJob
from contracts.views import process_pdf_job


def claim_next_job(stale_after):
    """Réserve atomiquement le prochain job (PENDING ou PROCESSING périmé)."""
    cutoff = timezone.now() - stale_after
    with transaction.atomic():
        job = (
            PdfJob.objects.select_for_update(skip_locked=True)
            .filter(status="PENDING")
            .order_by("id")
            .first()
        ) or (
            PdfJob.objects.select_for_update(skip_locked=True)
            .filter(status="PROCESSING", updated_at__lt=cutoff)
            .order_by("id")
            .first()
        )
        if job is None:
            return None
        job.status = "PROCESSING"
        job.save(update_fields=["status", "updated_at"])
        return job.id


class Command(BaseCommand):
    help = "Traite les PdfJob en attente (et reprend ceux restés bloqués)."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Boucle infinie")
        parser.add_argument("--interval", type=int, default=5)
        parser.add_argument(
            "--stale-minutes",
            type=int,
            default=10,
            help="Un job PROCESSING plus vieux que ça est repris.",
        )

    def handle(self, *args, **opts):
        stale = timedelta(minutes=opts["stale_minutes"])
        while True:
            done = 0
            while (job_id := claim_next_job(stale)) is not None:
                process_pdf_job(job_id)
                done += 1
            if done:
                self.stdout.write(f"{done} job(s) traité(s)")
            if not opts["loop"]:
                return
            time.sleep(opts["interval"])
