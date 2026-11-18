from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from payments.test_invoicing import setup_agency


class AuditLogTests(TestCase):
    def setUp(self):
        self.ag, self.user, self.contract = setup_agency("AU")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_vehicle_changes_are_recorded_with_user(self):
        vehicle = self.contract.vehicle
        # Le middleware HistoryRequestMiddleware renseigne l'utilisateur via la requête.
        res = self.api.patch(
            f"/api/vehicles/{vehicle.id}/", {"prix_par_jour": "350.00"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.content)

        log = self.api.get(f"/api/audit/?model=vehicle&id={vehicle.id}")
        self.assertEqual(log.status_code, 200)
        entries = log.json()
        self.assertEqual(entries[0]["type"], "updated")
        self.assertEqual(entries[0]["user"], self.user.get_username())
        fields = {c["field"]: c for c in entries[0]["changes"]}
        self.assertIn("prix_par_jour", fields)
        self.assertEqual(Decimal(fields["prix_par_jour"]["new"]), Decimal("350.00"))
        self.assertEqual(entries[-1]["type"], "created")

    def test_requires_valid_parameters(self):
        self.assertEqual(self.api.get("/api/audit/?model=nope&id=1").status_code, 400)
        self.assertEqual(
            self.api.get("/api/audit/?model=vehicle&id=abc").status_code, 400
        )

    def test_other_agency_history_is_hidden(self):
        _, _, other_contract = setup_agency("AV")
        other_vehicle = other_contract.vehicle
        res = self.api.get(f"/api/audit/?model=vehicle&id={other_vehicle.id}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), [])
