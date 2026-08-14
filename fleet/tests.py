from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from agency.models import Agency
from fleet import views
from fleet.models import Brand, GpsDevice, ModelCar, Vehicle, VehiclePriceInterval
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
        'prix_par_jour': Decimal('250.00'),
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
        # L'instance conservǸe peut avoir un cache gps_device pǸrimǸ : le push
        # doit lire le lien en base, pas depuis le cache.
        v = make_vehicle(matricule='TEST-G', kilometrage=100)
        device = GpsDevice.objects.create(agency=v.agency, vehicle=v, traccar_device_id=55)
        with mock.patch('fleet.views.update_device_accumulators') as m:
            views._push_kilometrage_to_traccar(v, v.agency)
        m.assert_called_once_with(55, total_distance_m=100000, agency=v.agency)


class MatriculeActuelTests(TestCase):
    def test_sans_date_mise_en_circulation_retourne_matricule(self):
        v = make_vehicle(matricule='12345-A-12', matricule_definitif=None)
        self.assertEqual(v.matricule_actuel, '12345-A-12')

    def test_definitif_avec_date_mais_avant_un_mois_retourne_provisoire(self):
        v = make_vehicle(matricule='WW12345', matricule_definitif='12345-A-12',
                         date_mise_en_circulation=date(2026, 7, 20))
        with mock.patch('fleet.models._today', return_value=date(2026, 8, 1)):
            self.assertEqual(v.matricule_actuel, 'WW12345')

    def test_definitif_apres_un_mois_retourne_definitif(self):
        v = make_vehicle(matricule='WW12345', matricule_definitif='12345-A-12',
                         date_mise_en_circulation=date(2026, 6, 20))
        with mock.patch('fleet.models._today', return_value=date(2026, 7, 20)):
            self.assertEqual(v.matricule_actuel, '12345-A-12')

    def test_definitif_manquant_apres_un_mois_retourne_matricule(self):
        v = make_vehicle(matricule='WW12345', matricule_definitif=None,
                         date_mise_en_circulation=date(2026, 6, 20))
        with mock.patch('fleet.models._today', return_value=date(2026, 8, 1)):
            self.assertEqual(v.matricule_actuel, 'WW12345')


class SeasonalPricingTests(TestCase):
    def test_prix_defaut_sans_intervalle(self):
        v = make_vehicle(prix_par_jour=Decimal('100.00'))
        self.assertEqual(v.prix_pour_date(date(2026, 3, 10)), Decimal('100.00'))

    def test_intervalle_absolu(self):
        v = make_vehicle(prix_par_jour=Decimal('100.00'))
        VehiclePriceInterval.objects.create(vehicle=v, type='ABSOLUTE', prix=Decimal('250.00'),
                                            date_debut=date(2026, 7, 1), date_fin=date(2026, 8, 31))
        self.assertEqual(v.prix_pour_date(date(2026, 7, 15)), Decimal('250.00'))
        self.assertEqual(v.prix_pour_date(date(2026, 9, 1)), Decimal('100.00'))

    def test_intervalle_recurrent_avec_chevauchement_annee(self):
        v = make_vehicle(prix_par_jour=Decimal('100.00'))
        VehiclePriceInterval.objects.create(vehicle=v, type='RECURRENT', prix=Decimal('300.00'),
                                            mois_debut=12, jour_debut=20,
                                            mois_fin=1, jour_fin=10)
        self.assertEqual(v.prix_pour_date(date(2027, 1, 5)), Decimal('300.00'))
        self.assertEqual(v.prix_pour_date(date(2027, 12, 25)), Decimal('300.00'))
        self.assertEqual(v.prix_pour_date(date(2027, 2, 1)), Decimal('100.00'))

    def test_absolu_prime_sur_recurrent(self):
        v = make_vehicle(prix_par_jour=Decimal('100.00'))
        VehiclePriceInterval.objects.create(vehicle=v, type='RECURRENT', prix=Decimal('400.00'),
                                            mois_debut=7, jour_debut=1, mois_fin=7, jour_fin=31)
        VehiclePriceInterval.objects.create(vehicle=v, type='ABSOLUTE', prix=Decimal('200.00'),
                                            date_debut=date(2026, 7, 10), date_fin=date(2026, 7, 12))
        self.assertEqual(v.prix_pour_date(date(2026, 7, 11)), Decimal('200.00'))

    def test_plus_cher_gagne_entre_memes_type(self):
        v = make_vehicle(prix_par_jour=Decimal('100.00'))
        VehiclePriceInterval.objects.create(vehicle=v, type='RECURRENT', prix=Decimal('150.00'),
                                            mois_debut=7, jour_debut=1, mois_fin=7, jour_fin=31)
        VehiclePriceInterval.objects.create(vehicle=v, type='RECURRENT', prix=Decimal('250.00'),
                                            mois_debut=7, jour_debut=5, mois_fin=7, jour_fin=20)
        self.assertEqual(v.prix_pour_date(date(2026, 7, 10)), Decimal('250.00'))

    def test_prix_pour_periode_somme_par_jour(self):
        v = make_vehicle(prix_par_jour=Decimal('100.00'))
        VehiclePriceInterval.objects.create(vehicle=v, type='RECURRENT', prix=Decimal('200.00'),
                                            mois_debut=7, jour_debut=1, mois_fin=7, jour_fin=31)
        quote = v.prix_pour_periode(date(2026, 6, 30), date(2026, 7, 2))
        self.assertEqual(quote['jours'], 3)
        self.assertEqual(quote['total'], Decimal('500.00'))  # 100 + 200 + 200
        self.assertEqual(quote['prix_moyen'], Decimal('500.00') / Decimal(3))


class ContractSeasonalTotalTests(TestCase):
    """Le montant total d'un contrat suit la somme saisonnière (sauf prix manuel)."""

    def _client(self, agency):
        from clients.models import Client
        return Client.objects.create(agency=agency, nom='Test', prenom='Client', telephone='0600000000')

    def _contract(self, v, prix, jours=2, start=None):
        from datetime import datetime, timezone as dt_timezone
        from contracts.models import Contract
        start = start or datetime(2026, 7, 1, 10, 0, tzinfo=dt_timezone.utc)
        return Contract.objects.create(
            agency=v.agency,
            vehicle=v,
            client=self._client(v.agency),
            date_sortie=start,
            date_retour_prevue=start,
            jours=jours,
            km_sortie=0,
            carburant_sortie='4/8',
            prix_par_jour=prix,
            statut='RESERVE',
        )

    def test_total_saisonnier_sur_prix_defaut(self):
        v = make_vehicle(prix_par_jour=Decimal('100.00'))
        VehiclePriceInterval.objects.create(vehicle=v, type='RECURRENT', prix=Decimal('200.00'),
                                            mois_debut=7, jour_debut=1, mois_fin=7, jour_fin=31)
        c = self._contract(v, prix=v.prix_par_jour)
        self.assertEqual(c.montant_total, Decimal('400.00'))  # 2 jours × 200
        self.assertEqual(c.prix_par_jour, Decimal('200.00'))

    def test_prix_manuel_prime(self):
        v = make_vehicle(prix_par_jour=Decimal('100.00'))
        VehiclePriceInterval.objects.create(vehicle=v, type='RECURRENT', prix=Decimal('200.00'),
                                            mois_debut=7, jour_debut=1, mois_fin=7, jour_fin=31)
        c = self._contract(v, prix=Decimal('150.00'))
        self.assertEqual(c.montant_total, Decimal('300.00'))  # 150 × 2
        self.assertEqual(c.prix_par_jour, Decimal('150.00'))

    def test_sans_intervalle_prix_par_jour_fois_jours(self):
        v = make_vehicle(prix_par_jour=Decimal('100.00'))
        c = self._contract(v, prix=v.prix_par_jour)
        self.assertEqual(c.montant_total, Decimal('200.00'))
        self.assertEqual(c.prix_par_jour, Decimal('100.00'))


class PublicPriceQuoteAPITests(TestCase):
    """Devis saisonnier exposé aux visiteurs via public-vehicles (sans auth)."""

    def test_price_quote_public_sans_auth(self):
        from rest_framework.test import APIClient
        v = make_vehicle(prix_par_jour=Decimal('100.00'))
        VehiclePriceInterval.objects.create(vehicle=v, type='RECURRENT', prix=Decimal('200.00'),
                                            mois_debut=7, jour_debut=1, mois_fin=7, jour_fin=31)
        res = APIClient().get(
            f'/api/public-vehicles/{v.pk}/price-quote/',
            {'start': '2026-06-30T10:00:00', 'end': '2026-07-02T10:00:00'},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['jours'], 3)
        self.assertEqual(Decimal(res.data['total']), Decimal('500.00'))

    def test_price_quote_public_dates_manquantes(self):
        from rest_framework.test import APIClient
        v = make_vehicle()
        res = APIClient().get(f'/api/public-vehicles/{v.pk}/price-quote/')
        self.assertEqual(res.status_code, 400)


