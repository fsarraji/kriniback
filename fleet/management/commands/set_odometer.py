"""Corrige le kilométrage (odomètre) d'un véhicule sur le serveur Traccar.

Utilise l'endpoint officiel Traccar PUT /api/devices/{id}/accumulators qui met à
jour la dernière position du dispositif (totalDistance en mètres).

Usage :
  python manage.py set_odometer <vehicule> <km> [options]

<vehicule>  : id (nombre) ou matricule du véhicule Krini, ou ID du dispositif
              Traccar si --device-id est précisé.

Options :
  --device-id <id>   Interprète <vehicule> comme un ID de dispositif Traccar
                     (nécessite --agency ou une config Traccar globale).
  --agency <id>      Identifiants Traccar de l'agence (URL, user, password).
  --hours <h>        Corrige aussi les heures moteur du dispositif.
  --update-krini     Met aussi à jour le champ kilometrage du véhicule dans Krini.
  --dry-run          Affiche l'état actuel sans rien modifier.

Exemples :
  python manage.py set_odometer 7 45200
  python manage.py set_odometer AB-123-45 45200 --update-krini
  python manage.py set_odometer 123456 45200 --device-id --agency 2
"""
from django.core.management.base import BaseCommand, CommandError

from fleet.models import Vehicle
from fleet.traccar import (
    TraccarError,
    TraccarNotConfigured,
    get_device_position,
    normalize_position,
    update_device_accumulators,
)


class Command(BaseCommand):
    help = "Corrige le kilométrage (odomètre) d'un véhicule sur le serveur Traccar."

    def add_arguments(self, parser):
        parser.add_argument('vehicle', help="id ou matricule du véhicule Krini (ou ID Traccar avec --device-id).")
        parser.add_argument('km', type=float, help="Nouvelle valeur de kilométrage en km.")
        parser.add_argument('--device-id', action='store_true', help="Interpréter <vehicle> comme un ID de dispositif Traccar.")
        parser.add_argument('--agency', type=int, help="ID de l'agence dont on utilise la config Traccar.")
        parser.add_argument('--hours', type=float, help="Nouvelle valeur des heures moteur (optionnel).")
        parser.add_argument('--update-krini', action='store_true', help="Mettre aussi à jour Vehicle.kilometrage dans Krini.")
        parser.add_argument('--dry-run', action='store_true', help="Afficher l'état actuel sans modifier.")

    def _resolve(self, ref, device_id_mode, agency_id):
        if device_id_mode:
            device_id = int(ref)
            vehicle = Vehicle.objects.filter(traccar_device_id=device_id).first()
            return device_id, vehicle
        vehicle = None
        if ref.isdigit():
            vehicle = Vehicle.objects.select_related('agency').filter(pk=int(ref)).first()
        if vehicle is None:
            vehicle = Vehicle.objects.select_related('agency').filter(matricule=ref).first()
        if vehicle is None:
            raise CommandError(
                f"Aucun véhicule Krini trouvé pour « {ref} » (id ou matricule). "
                "Utilisez --device-id pour viser directement un dispositif Traccar."
            )
        if not vehicle.traccar_device_id:
            raise CommandError(
                f"Le véhicule {vehicle.matricule} (id={vehicle.id}) n'a pas de dispositif "
                "Traccar associé (traccar_device_id vide)."
            )
        return vehicle.traccar_device_id, vehicle

    def handle(self, *args, **options):
        ref = options['vehicle']
        km = options['km']
        hours = options['hours']
        device_id_mode = options['device_id']
        agency_id = options['agency']
        update_krini = options['update_krini']
        dry_run = options['dry_run']

        if km < 0:
            raise CommandError("Le kilométrage doit être positif.")
        if hours is not None and hours < 0:
            raise CommandError("Les heures moteur doivent être positives.")

        device_id, vehicle = self._resolve(ref, device_id_mode, agency_id)

        agency = None
        if agency_id is not None:
            from agency.models import Agency
            agency = Agency.objects.filter(pk=agency_id).first()
            if agency is None:
                raise CommandError(f"Agence {agency_id} introuvable.")
        elif vehicle is not None:
            agency = vehicle.agency

        try:
            before = normalize_position(get_device_position(device_id, agency=agency))
        except TraccarNotConfigured:
            raise CommandError(
                "Traccar n'est pas configuré (ni pour l'agence ni via les variables "
                "TRACCAR_URL / TRACCAR_USER / TRACCAR_PASSWORD)."
            )
        except TraccarError as exc:
            raise CommandError(f"Impossible de joindre Traccar : {exc}")

        target_vehicle = f"vehicule {vehicle.matricule} (id={vehicle.id})" if vehicle else f"dispositif Traccar {device_id}"

        self.stdout.write(f"=== Correction kilométrage : {target_vehicle} ===")
        self.stdout.write(f"Dispositif Traccar  : {device_id}")
        self.stdout.write(f"Nouvelle valeur     : {km:g} km ({(km * 1000):g} m)")
        if hours is not None:
            self.stdout.write(f"Heures moteur       : {hours:g} h")

        odometer_before = (before or {}).get('odometer')
        if odometer_before is not None:
            self.stdout.write(f"Kilométrage actuel  : {(odometer_before / 1000):g} km ({odometer_before:g} m)")
        else:
            self.stdout.write("Kilométrage actuel  : inconnu (aucune position récente)")

        if dry_run:
            self.stdout.write(self.style.WARNING("Mode dry-run : aucune modification effectuée."))
            return

        try:
            update_device_accumulators(
                device_id,
                total_distance_m=km * 1000,
                hours=hours,
                agency=agency,
            )
        except TraccarError as exc:
            raise CommandError(f"Échec de la mise à jour sur Traccar : {exc}")

        after = normalize_position(get_device_position(device_id, agency=agency))
        odometer_after = (after or {}).get('odometer')
        if odometer_after is not None:
            self.stdout.write(self.style.SUCCESS(
                f"Kilométrage après    : {(odometer_after / 1000):g} km ({odometer_after:g} m)"
            ))
        else:
            self.stdout.write(self.style.WARNING("Kilométrage après : position non récupérée, vérifiez sur Traccar."))

        if (after or {}).get('odometer') and odometer_before and odometer_after == odometer_before:
            self.stdout.write(self.style.WARNING(
                "Attention : la valeur affichée peut encore provenir de l'attribut "
                "odometer renvoyé directement par le dispositif GPS (il prime sur le "
                "totalDistance du serveur dans Krini). Utilisez --update-krini pour "
                "synchroniser le champ kilometrage du véhicule."
            ))

        if update_krini:
            if vehicle is None:
                raise CommandError("--update-krini nécessite un véhicule Krini (sans --device-id).")
            vehicle.kilometrage = int(round(km))
            vehicle.save(update_fields=['kilometrage'])
            self.stdout.write(self.style.SUCCESS(f"Vehicle.kilometrage mis à jour : {vehicle.kilometrage} km."))
