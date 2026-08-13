from django.db import migrations


def normalize_vehicle_statut(apps, schema_editor):
    """Corrige les statuts de véhicules dont la casse est erronée.

    Certaines données contiennent 'AVAILABLE'/'available' au lieu de la valeur
    canonique 'Available'. Les fronts web/mobile filtrent sur la valeur exacte,
    ce qui masquait le bouton « Louer » et excluait le véhicule du formulaire
    de contrat. On ramène ici toutes les variantes vers les valeurs canoniques.
    """
    Vehicle = apps.get_model('fleet', 'Vehicle')
    STATUS_MAP = {'available': 'Available', 'rented': 'Rented', 'maintenance': 'Maintenance'}
    updated = 0
    for vehicle in Vehicle.objects.exclude(statut__in=['Available', 'Rented', 'Maintenance']).iterator():
        canonical = STATUS_MAP.get((vehicle.statut or '').strip().lower())
        if canonical and vehicle.statut != canonical:
            vehicle.statut = canonical
            vehicle.save(update_fields=['statut'])
            updated += 1
    print(f'[migration] {updated} véhicule(s) avec un statut normalisé')


class Migration(migrations.Migration):

    dependencies = [
        ('fleet', '0014_gps_device'),
    ]

    operations = [
        migrations.RunPython(normalize_vehicle_statut, migrations.RunPython.noop),
    ]
