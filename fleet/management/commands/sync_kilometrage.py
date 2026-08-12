"""Synchronise le kilométrage des véhicules Krini depuis le serveur Traccar.

Pour chaque véhicule lié à un dispositif Traccar, récupère la dernière position
et met à jour Vehicle.kilometrage (km entiers) si la valeur a changé. Traccar
est la source de vérité du kilométrage.

Usage :
  python manage.py sync_kilometrage [matricule|id ...] [--dry-run]

Sans argument : tous les véhicules équipés d'un dispositif Traccar.

Exemples :
  python manage.py sync_kilometrage
  python manage.py sync_kilometrage 49583-A-49 7 --dry-run
"""
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

from fleet.models import Vehicle
from fleet.traccar import TraccarError, TraccarNotConfigured, get_positions, normalize_position


class Command(BaseCommand):
    help = "Synchronise Vehicle.kilometrage depuis la dernière position Traccar."

    def add_arguments(self, parser):
        parser.add_argument(
            'vehicles',
            nargs='*',
            help="Matricules ou ids (optionnel : tous les véhicules Traccar par défaut).",
        )
        parser.add_argument('--dry-run', action='store_true', help="Affiche les changements sans les appliquer.")

    def _resolve(self, ref):
        vehicle = None
        if str(ref).isdigit():
            vehicle = Vehicle.objects.select_related('agency', 'gps_device').filter(pk=int(ref)).first()
        if vehicle is None:
            vehicle = Vehicle.objects.select_related('agency', 'gps_device').filter(matricule=ref).first()
        if vehicle is None:
            raise CommandError(f"Véhicule introuvable : {ref}")
        return vehicle

    def handle(self, *args, **options):
        refs = options['vehicles']
        dry_run = options['dry_run']

        if refs:
            vehicles = [self._resolve(ref) for ref in refs]
        else:
            vehicles = list(
                Vehicle.objects.select_related('agency', 'gps_device').filter(gps_device__isnull=False)
            )

        if not vehicles:
            self.stdout.write(self.style.WARNING("Aucun véhicule à synchroniser."))
            return

        by_agency = defaultdict(list)
        for vehicle in vehicles:
            by_agency[vehicle.agency_id].append(vehicle)

        updated = unchanged = skipped = errors = 0

        for agency_id, group in by_agency.items():
            agency = group[0].agency
            try:
                positions = {
                    p.get('deviceId'): p
                    for p in get_positions(agency=agency)
                    if p.get('deviceId')
                }
            except TraccarNotConfigured:
                for vehicle in group:
                    self.stdout.write(self.style.WARNING(
                        f"[{vehicle.matricule}] Traccar non configuré pour l'agence {agency_id} — ignoré."
                    ))
                    skipped += 1
                continue
            except TraccarError as exc:
                for vehicle in group:
                    self.stdout.write(self.style.ERROR(f"[{vehicle.matricule}] Erreur Traccar : {exc}"))
                    errors += 1
                continue

            for vehicle in group:
                device_id = vehicle.traccar_device_id
                if not device_id:
                    continue
                position = normalize_position(positions.get(device_id))
                if not position or position.get('odometer') is None:
                    self.stdout.write(self.style.WARNING(
                        f"[{vehicle.matricule}] Pas de position / kilométrage Traccar."
                    ))
                    skipped += 1
                    continue

                new_km = int(round(position['odometer'] / 1000.0))
                old_km = vehicle.kilometrage
                if new_km == old_km:
                    self.stdout.write(f"[{vehicle.matricule}] kilométrage inchangé : {new_km} km")
                    unchanged += 1
                    continue

                if dry_run:
                    self.stdout.write(f"[{vehicle.matricule}] {old_km} -> {new_km} km (dry-run)")
                else:
                    vehicle.kilometrage = new_km
                    vehicle.save(update_fields=['kilometrage'])
                    self.stdout.write(self.style.SUCCESS(
                        f"[{vehicle.matricule}] {old_km} -> {new_km} km"
                    ))
                updated += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Terminé : {updated} mis à jour, {unchanged} inchangés, {skipped} ignorés, {errors} erreurs."
        ))
