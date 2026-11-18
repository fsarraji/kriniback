from datetime import timedelta

from django.test import TestCase
from rest_framework.test import APIClient

from payments.test_invoicing import setup_agency


class FreeVehiclesTests(TestCase):
    def setUp(self):
        self.ag, self.user, self.contract = setup_agency("FR")
        self.vehicle = self.contract.vehicle
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def _free(self, start, end):
        return self.api.get(
            "/api/vehicles/free/",
            {"start": start.isoformat(), "end": end.isoformat()},
        )

    def test_busy_vehicle_excluded_during_contract(self):
        c = self.contract
        res = self._free(c.date_sortie, c.date_retour_prevue)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertNotIn(self.vehicle.id, res.json()["ids"])

    def test_vehicle_free_far_after_contract(self):
        c = self.contract
        start = c.date_retour_prevue + timedelta(days=30)
        res = self._free(start, start + timedelta(days=2))
        self.assertIn(self.vehicle.id, res.json()["ids"])

    def test_maintenance_vehicle_never_free(self):
        self.vehicle.statut = "Maintenance"
        self.vehicle.save()
        start = self.contract.date_retour_prevue + timedelta(days=30)
        res = self._free(start, start + timedelta(days=2))
        self.assertNotIn(self.vehicle.id, res.json()["ids"])

    def test_requires_valid_period(self):
        res = self.api.get(
            "/api/vehicles/free/", {"start": "2026-01-02", "end": "2026-01-01"}
        )
        self.assertEqual(res.status_code, 400)

    def test_other_agency_vehicles_not_listed(self):
        _, _, other_contract = setup_agency("FX")
        start = other_contract.date_retour_prevue + timedelta(days=30)
        res = self._free(start, start + timedelta(days=2))
        self.assertNotIn(other_contract.vehicle.id, res.json()["ids"])
