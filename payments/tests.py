from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from agency.models import Agency, CustomUser, Subscription
from clients.models import Client
from contracts.models import BookingRequest, Contract
from fleet.models import Brand, ModelCar, Vehicle
from payments.models import Payment

# Create your tests here.


def _setup(name):
    ag = Agency.objects.create(nom_agence=name, adresse="x", telephone="0600000000")
    Subscription.objects.create(
        agency=ag,
        start_date=timezone.localdate() - timedelta(days=1),
        end_date=timezone.localdate() + timedelta(days=30),
    )
    user = CustomUser.objects.create_user(
        f"u{name}", password="pw12345", agency=ag, role="OWNER"
    )
    brand = Brand.objects.create(name=f"B{name}")
    model = ModelCar.objects.create(name=f"M{name}", brand=brand)
    veh = Vehicle.objects.create(
        agency=ag,
        matricule=f"1-A-{name}",
        marque=brand,
        modele=model,
        annee=2020,
        kilometrage=1000,
        prix_par_jour=Decimal("300"),
        prochain_vidange_km=20000,
        date_assurance=timezone.localdate() + timedelta(days=200),
        date_visite_technique=timezone.localdate() + timedelta(days=200),
    )
    cl = Client.objects.create(agency=ag, nom="N", prenom="P", telephone="0611111111")
    return ag, user, veh, cl


class AgencyIsolationAndPaymentTests(TestCase):
    def setUp(self):
        self.a = _setup("A")
        self.b = _setup("B")
        now = timezone.now()
        self.contract_b = Contract.objects.create(
            agency=self.b[0],
            vehicle=self.b[2],
            client=self.b[3],
            km_sortie=0,
            date_sortie=now,
            date_retour_prevue=now + timedelta(days=2),
            jours=2,
            prix_par_jour=Decimal("300"),
            statut="RESERVE",
        )
        self.api = APIClient()
        self.api.force_authenticate(self.a[1])

    def test_cannot_pay_other_agency_contract(self):
        r = self.api.post(
            "/api/payments/", {"contract": self.contract_b.id, "amount": "50"}
        )
        self.assertEqual(r.status_code, 400)
        self.contract_b.refresh_from_db()
        self.assertEqual(self.contract_b.montant_paye, 0)

    def test_payment_update_and_delete_adjust_contract(self):
        p = Payment.objects.create(
            agency=self.b[0], contract=self.contract_b, amount=Decimal("100")
        )
        self.contract_b.refresh_from_db()
        self.assertEqual(self.contract_b.montant_paye, 100)
        p.amount = Decimal("250")
        p.save()
        self.contract_b.refresh_from_db()
        self.assertEqual(self.contract_b.montant_paye, 250)
        p.delete()
        self.contract_b.refresh_from_db()
        self.assertEqual(self.contract_b.montant_paye, 0)

    def test_public_vehicle_hides_sensitive_fields(self):
        from fleet.serializers import PublicVehicleSerializer

        veh = self.a[2]
        veh.km_loue = (
            0  # évite la requête agrégée (le test contracts définit un modèle homonyme)
        )
        data = PublicVehicleSerializer(veh).data
        for f in ("gps_imei", "sim_number", "date_assurance", "traccar_device_id"):
            self.assertNotIn(f, data)
        self.assertIn("prix_par_jour", data)

    def test_booking_confirm_works(self):
        now = timezone.now()
        bk = BookingRequest.objects.create(
            agency=self.a[0],
            vehicle=self.a[2],
            nom="X",
            prenom="Y",
            telephone="0699999999",
            date_sortie=now + timedelta(days=5),
            date_retour_prevue=now + timedelta(days=7),
        )
        r = self.api.post(f"/api/booking-requests/{bk.id}/confirm/")
        self.assertEqual(r.status_code, 201, r.content)

    def test_users_endpoint_superuser_only(self):
        self.a[1].is_staff = True
        self.a[1].save()
        self.assertEqual(self.api.get("/api/users/").status_code, 403)
