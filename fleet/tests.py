from datetime import date
from unittest import mock

from django.test import TestCase

from agency.models import Agency
from fleet import views
from fleet.models import Brand, GpsDevice, ModelCar, Vehicle
from fleet.traccar import TraccarNotConfigured


def make_vehicle(**kwargs):
    agency = Agency.objects.create(
        nom_agence="Agence Test",
        adresse="Casablanca",
        telephone="0600000000",
    )
    brand = Brand.objects.create(name="Dacia")
    modele = ModelCar.objects.create(brand=brand, name="Sandero")
    defaults = {
        'agency': agency,
        'matricule': 'TEST-001',
        'marque': brand,
        'modele': modele,
        'annee': 2020,
        'couleur': 'Blanc',
        'carburant': 'Diesel',
        'kilometrage': 45000,
        'prix_par_jour': '250.00',
        'date_assurance': date(2026, 1, 1),
        'date_visite_technique': date(2026, 1, 1),
        'prochain_vidange_km': 60000,
    }
    defaults.update(kwargs)
    return Vehicle.objects.create(**defaults)


class GpsLinkRegressionTests(TestCase):
    def test_traccar_device_id_property_without_device(self):
        v = make_vehicle(matricule='TEST-A')
        fresh = Vehicle.objects.get(pk=v.pk)  # sans select_related : pas de cache gps_device
        self.assertIsNone(fresh.traccar_device_id)

    def test_traccar_device_id_property_with_device(self):
        v = make_vehicle(matricule='TEST-B')
        GpsDevice.objects.create(agency=v.agency, vehicle=v, traccar_device_id=99)
        self.assertEqual(Vehicle.objects.get(pk=v.pk).traccar_device_id, 99)

    def test_sync_traccar_device_no_crash_without_device(self):
        # Bug prod : AttributeError 'Vehicle' object has no attribute 'gps_device_id'
        v = make_vehicle(matricule='TEST-C', gps_imei='123456789')
        with mock.patch('fleet.views.create_device', side_effect=TraccarNotConfigured):
            views._sync_traccar_device(v, v.agency)  # ne doit pas lever d'exception

    def test_sync_traccar_device_skips_when_device_linked(self):
        v = make_vehicle(matricule='TEST-F', gps_imei='123456789')
        GpsDevice.objects.create(agency=v.agency, vehicle=v, traccar_device_id=7)
        with mock.patch('fleet.views.create_device') as create:
            views._sync_traccar_device(v, v.agency)
        create.assert_not_called()

    def test_push_kilometrage_with_device(self):
        v = make_vehicle(matricule='TEST-D', kilometrage=12345)
        GpsDevice.objects.create(agency=v.agency, vehicle=v, traccar_device_id=42)
        with mock.patch('fleet.views.update_device_accumulators') as m:
            views._push_kilometrage_to_traccar(v, v.agency)
        m.assert_called_once_with(42, total_distance_m=12345000, agency=v.agency)

    def test_push_kilometrage_without_device_no_crash(self):
        v = make_vehicle(matricule='TEST-E', kilometrage=12345)
        fresh = Vehicle.objects.get(pk=v.pk)  # sans cache gps_device
        with mock.patch('fleet.views.update_device_accumulators') as m:
            views._push_kilometrage_to_traccar(fresh, fresh.agency)
        m.assert_not_called()

    def test_push_kilometrage_uses_fresh_db_link_after_attach(self):
        # L'instance conservée peut avoir un cache gps_device périmé : le push
        # doit lire le lien en base, pas depuis le cache.
        v = make_vehicle(matricule='TEST-G', kilometrage=100)
        device = GpsDevice.objects.create(agency=v.agency, vehicle=v, traccar_device_id=55)
        with mock.patch('fleet.views.update_device_accumulators') as m:
            views._push_kilometrage_to_traccar(v, v.agency)
        m.assert_called_once_with(55, total_distance_m=100000, agency=v.agency)
