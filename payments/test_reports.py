from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from agency.models import CustomUser
from contracts.models import Contract
from expenses.models import Expense
from payments.models import Payment
from payments.test_invoicing import setup_agency


class ReportsTests(TestCase):
    def setUp(self):
        self.ag, self.owner, self.contract = setup_agency("R")
        self.vehicle = self.contract.vehicle
        self.today = timezone.localdate()
        now = timezone.now()
        # Contrat terminé : 2 jours pleins dans la période
        Contract.objects.filter(pk=self.contract.pk).update(
            statut="TERMINE",
            date_sortie=now - timedelta(days=3),
            date_retour_prevue=now - timedelta(days=1),
            date_retour_effective=now - timedelta(days=1),
        )
        self.contract.refresh_from_db()
        Payment.objects.create(
            agency=self.ag,
            contract=self.contract,
            amount=Decimal("500"),
            payment_method="Espèce",
            user=self.owner,
        )
        Payment.objects.create(
            agency=self.ag,
            contract=self.contract,
            amount=Decimal("100"),
            payment_method="TPE",
            user=self.owner,
        )
        Expense.objects.create(
            agency=self.ag,
            vehicle=self.vehicle,
            category="Maintenance",
            title="Vidange",
            amount=Decimal("150"),
            expense_date=self.today,
        )
        Expense.objects.create(
            agency=self.ag,
            category="Rent",
            title="Loyer",
            amount=Decimal("1000"),
            expense_date=self.today,
        )
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def _range(self):
        return {
            "from": (self.today - timedelta(days=9)).isoformat(),
            "to": self.today.isoformat(),
        }

    def test_occupancy(self):
        r = self.api.get("/api/reports/occupancy/", self._range())
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertEqual(data["period_days"], 10)
        v = data["vehicles"][0]
        self.assertAlmostEqual(v["days_rented"], 2.0, places=1)
        self.assertAlmostEqual(v["occupancy_rate"], 20.0, places=0)

    def test_vehicle_profitability(self):
        data = self.api.get("/api/reports/vehicle-profitability/", self._range()).json()
        v = data["vehicles"][0]
        self.assertEqual(
            (v["revenue"], v["expenses"], v["net"]), ("600.00", "150.00", "450.00")
        )
        self.assertEqual(data["totals"]["general_expenses"], "1000.00")
        self.assertEqual(data["totals"]["net_after_all_expenses"], "-550.00")

    def test_vehicle_name_not_duplicated(self):
        data = self.api.get("/api/reports/vehicle-profitability/", self._range()).json()
        self.assertEqual(data["vehicles"][0]["vehicle"], "Dacia Logan")
        occ = self.api.get("/api/reports/occupancy/", self._range()).json()
        self.assertEqual(occ["vehicles"][0]["vehicle"], "Dacia Logan")

    def test_top_clients(self):
        data = self.api.get("/api/reports/top-clients/", self._range()).json()
        self.assertEqual(len(data["clients"]), 1)
        self.assertEqual(data["clients"][0]["contracts"], 1)

    def test_revenue_by_month(self):
        data = self.api.get("/api/reports/revenue/", {"year": self.today.year}).json()
        self.assertEqual(len(data["months"]), 12)
        self.assertEqual(data["totals"]["revenue"], "600.00")
        self.assertEqual(data["totals"]["expenses"], "1150.00")
        self.assertEqual(data["totals"]["net"], "-550.00")

    def test_cash_journal_and_csv(self):
        data = self.api.get("/api/reports/cash-journal/").json()
        self.assertEqual(data["total_collected"], "600.00")
        self.assertEqual(data["cash_collected"], "500.00")
        self.assertEqual(
            data["totals_by_method"], {"Espèce": "500.00", "TPE": "100.00"}
        )
        self.assertEqual(data["total_expenses"], "1150.00")
        r = self.api.get("/api/reports/cash-journal/", {"export": "csv"})
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(len(r.content.decode("utf-8-sig").strip().splitlines()), 3)

    def test_isolation_between_agencies(self):
        _, other_owner, _ = setup_agency("Z")
        api = APIClient()
        api.force_authenticate(other_owner)
        data = api.get("/api/reports/revenue/", {"year": self.today.year}).json()
        self.assertEqual(data["totals"]["revenue"], "0.00")
        self.assertEqual(
            api.get("/api/reports/cash-journal/").json()["total_collected"], "0.00"
        )

    def test_financial_reports_require_owner(self):
        emp = CustomUser.objects.create_user(
            "emp_r", password="pw12345", agency=self.ag, role="EMPLOYEE"
        )
        api = APIClient()
        api.force_authenticate(emp)
        for path in ("revenue", "vehicle-profitability", "top-clients"):
            self.assertEqual(api.get(f"/api/reports/{path}/").status_code, 403, path)
        self.assertEqual(api.get("/api/reports/occupancy/").status_code, 200)
        self.assertEqual(api.get("/api/reports/cash-journal/").status_code, 200)

    def test_bad_params(self):
        self.assertEqual(
            self.api.get(
                "/api/reports/occupancy/", {"from": "2026-01-10", "to": "2026-01-01"}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.api.get("/api/reports/occupancy/", {"from": "abc"}).status_code, 400
        )
        self.assertEqual(
            self.api.get("/api/reports/cash-journal/", {"date": "nope"}).status_code,
            400,
        )
        self.assertEqual(
            self.api.get("/api/reports/revenue/", {"year": "x"}).status_code, 400
        )
        client = CustomUser.objects.create_user(
            "c_r", password="pw12345", role="CLIENT"
        )
        api = APIClient()
        api.force_authenticate(client)
        self.assertEqual(api.get("/api/reports/occupancy/").status_code, 403)
