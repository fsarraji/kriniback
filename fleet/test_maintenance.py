from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from agency.models import Agency, CustomUser, Notification, Subscription
from agency.notifications import generate_alerts_for_agency, run_alerts
from clients.models import Client
from contracts.models import Contract
from expenses.models import Expense
from fleet.models import Brand, MaintenanceRecord, ModelCar, Vehicle


def make_agency(name, email=None):
    ag = Agency.objects.create(
        nom_agence=name, adresse="x", telephone="0600000000", email=email
    )
    Subscription.objects.create(
        agency=ag,
        start_date=timezone.localdate() - timedelta(days=1),
        end_date=timezone.localdate() + timedelta(days=60),
    )
    user = CustomUser.objects.create_user(
        f"user_{name}", password="pw12345", agency=ag, role="OWNER"
    )
    brand, _ = Brand.objects.get_or_create(name="Dacia")
    model, _ = ModelCar.objects.get_or_create(brand=brand, name="Logan")
    return ag, user, brand, model


def make_vehicle(ag, brand, model, matricule, **kw):
    today = timezone.localdate()
    data = dict(
        agency=ag,
        matricule=matricule,
        marque=brand,
        modele=model,
        annee=2021,
        kilometrage=1000,
        prix_par_jour=Decimal("300"),
        date_assurance=today + timedelta(days=300),
        date_visite_technique=today + timedelta(days=300),
        prochain_vidange_km=50000,
    )
    data.update(kw)
    return Vehicle.objects.create(**data)


class MaintenanceAPITests(TestCase):
    def setUp(self):
        self.a = make_agency("A")
        self.b = make_agency("B")
        self.va = make_vehicle(self.a[0], self.a[2], self.a[3], "A-1")
        self.vb = make_vehicle(self.b[0], self.b[2], self.b[3], "B-1")
        self.api = APIClient()
        self.api.force_authenticate(self.a[1])

    def test_create_vidange_updates_vehicle_and_creates_expense(self):
        r = self.api.post(
            "/api/maintenance/",
            {
                "vehicle": self.va.id,
                "type": "VIDANGE",
                "km": 1000,
                "cost": "450.00",
                "prochain_km": 11000,
                "create_expense": True,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.va.refresh_from_db()
        self.assertEqual(self.va.prochain_vidange_km, 11000)
        rec = MaintenanceRecord.objects.get()
        self.assertEqual(rec.agency_id, self.a[0].id)
        self.assertEqual(Expense.objects.get().amount, Decimal("450.00"))
        self.assertEqual(rec.expense_id, Expense.objects.get().id)

    def test_cannot_use_other_agency_vehicle(self):
        r = self.api.post(
            "/api/maintenance/", {"vehicle": self.vb.id, "type": "PNEUS"}, format="json"
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(MaintenanceRecord.objects.count(), 0)

    def test_list_is_scoped_to_agency(self):
        MaintenanceRecord.objects.create(
            agency=self.b[0], vehicle=self.vb, type="PNEUS"
        )
        mine = MaintenanceRecord.objects.create(
            agency=self.a[0], vehicle=self.va, type="FREINS"
        )
        r = self.api.get("/api/maintenance/")
        ids = [row["id"] for row in r.json()["results"]]
        self.assertEqual(ids, [mine.id])


class AlertsTests(TestCase):
    def setUp(self):
        self.ag, self.user, self.brand, self.model = make_agency(
            "Al", "agence@example.com"
        )
        self.today = timezone.localdate()

    def test_generates_expected_alerts_and_is_idempotent(self):
        make_vehicle(
            self.ag,
            self.brand,
            self.model,
            "EXP-1",
            date_assurance=self.today - timedelta(days=3),
            date_visite_technique=self.today + timedelta(days=10),
            kilometrage=49800,
            prochain_vidange_km=50000,
        )
        make_vehicle(self.ag, self.brand, self.model, "OK-1")
        created = generate_alerts_for_agency(self.ag)
        kinds = sorted(n.kind for n in created)
        self.assertEqual(kinds, ["INSURANCE", "VIDANGE", "VISITE"])
        insurance = next(n for n in created if n.kind == "INSURANCE")
        self.assertEqual(insurance.level, "CRITICAL")
        # 2e passage : rien de nouveau
        self.assertEqual(generate_alerts_for_agency(self.ag), [])
        self.assertEqual(Notification.objects.count(), 3)

    def test_overdue_and_due_soon_returns(self):
        v1 = make_vehicle(self.ag, self.brand, self.model, "R-1")
        v2 = make_vehicle(self.ag, self.brand, self.model, "R-2")
        client = Client.objects.create(
            agency=self.ag, nom="N", prenom="P", telephone="06"
        )
        now = timezone.now()
        for v, delta in ((v1, timedelta(hours=-5)), (v2, timedelta(hours=5))):
            Contract.objects.create(
                agency=self.ag,
                vehicle=v,
                client=client,
                km_sortie=0,
                date_sortie=now - timedelta(days=2),
                date_retour_prevue=now + delta,
                jours=2,
                prix_par_jour=Decimal("300"),
                statut="EN_COURS",
            )
        kinds = sorted(n.kind for n in generate_alerts_for_agency(self.ag))
        self.assertEqual(kinds, ["RETURN_DUE", "RETURN_OVERDUE"])

    def test_email_digest_sent_once_and_marked(self):
        make_vehicle(
            self.ag,
            self.brand,
            self.model,
            "M-1",
            date_assurance=self.today + timedelta(days=5),
        )
        result = run_alerts(send_email=True)
        self.assertEqual(result[self.ag.id], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["agence@example.com"])
        self.assertIsNotNone(Notification.objects.get().emailed_at)
        run_alerts(send_email=True)
        self.assertEqual(len(mail.outbox), 1)

    def test_notification_api_scope_and_read_state(self):
        other = make_agency("Other")
        Notification.objects.create(
            agency=other[0], kind="VIDANGE", title="x", dedup_key="k1"
        )
        mine = Notification.objects.create(
            agency=self.ag, kind="VIDANGE", title="mine", dedup_key="k2"
        )
        api = APIClient()
        api.force_authenticate(self.user)
        self.assertEqual(api.get("/api/notifications/unread_count/").json()["count"], 1)
        self.assertEqual(
            api.post(f"/api/notifications/{mine.id}/mark_read/").status_code, 200
        )
        self.assertEqual(api.get("/api/notifications/unread_count/").json()["count"], 0)
        # Impossible d'accéder à la notification d'une autre agence
        foreign = Notification.objects.get(dedup_key="k1")
        self.assertEqual(
            api.post(f"/api/notifications/{foreign.id}/mark_read/").status_code, 404
        )
