from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from clients.models import Client
from contracts.models import Contract, Reservation
from fleet.models import VehiclePriceInterval
from payments.test_invoicing import setup_agency


def _api(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


class ProlongAvailabilityTests(TestCase):
    """Le prolongement doit refuser un chevauchement avec un autre contrat."""

    def setUp(self):
        self.ag, self.user, self.contract = setup_agency("P")
        self.contract.statut = "EN_COURS"
        self.contract.save()
        self.client_obj = self.contract.client
        self.api = _api(self.user)

    def _other_contract(self, start, end):
        return Contract.objects.create(
            agency=self.ag,
            vehicle=self.contract.vehicle,
            client=self.client_obj,
            km_sortie=0,
            date_sortie=start,
            date_retour_prevue=end,
            jours=max(1, (end - start).days),
            prix_par_jour=Decimal("300"),
            statut="RESERVE",
        )

    def test_prolong_blocked_by_following_contract(self):
        end = self.contract.date_retour_prevue
        self._other_contract(end + timedelta(days=1), end + timedelta(days=4))
        new_end = (end + timedelta(days=3)).isoformat()
        res = self.api.post(
            f"/api/contracts/{self.contract.id}/prolong/",
            {"date_retour_prevue": new_end},
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.date_retour_prevue, end)

    def test_prolong_allowed_when_free(self):
        end = self.contract.date_retour_prevue
        new_end = (end + timedelta(days=2)).isoformat()
        res = self.api.post(
            f"/api/contracts/{self.contract.id}/prolong/",
            {"date_retour_prevue": new_end},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)


class ContractUpdateAvailabilityTests(TestCase):
    """Déplacer un contrat sur une période occupée doit échouer."""

    def setUp(self):
        self.ag, self.user, self.contract = setup_agency("U")
        self.api = _api(self.user)

    def test_moving_dates_onto_other_contract_rejected(self):
        end = self.contract.date_retour_prevue
        later_start = end + timedelta(days=10)
        other = Contract.objects.create(
            agency=self.ag,
            vehicle=self.contract.vehicle,
            client=self.contract.client,
            km_sortie=0,
            date_sortie=later_start,
            date_retour_prevue=later_start + timedelta(days=3),
            jours=3,
            prix_par_jour=Decimal("300"),
            statut="RESERVE",
        )
        res = self.api.patch(
            f"/api/contracts/{self.contract.id}/",
            {
                "date_sortie": (later_start + timedelta(days=1)).isoformat(),
                "date_retour_prevue": (later_start + timedelta(days=2)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)
        other.refresh_from_db()


class ReservationConfirmAvailabilityTests(TestCase):
    def setUp(self):
        self.ag, self.user, self.contract = setup_agency("R")
        self.api = _api(self.user)

    def test_confirm_refused_when_vehicle_taken(self):
        # Le contrat existant occupe déjà [now, now+2j] ; la réservation chevauche.
        client = Client.objects.create(
            agency=self.ag, nom="B", prenom="C", telephone="0600"
        )
        reservation = Reservation.objects.create(
            agency=self.ag,
            vehicle=self.contract.vehicle,
            client=client,
            date_sortie=self.contract.date_sortie + timedelta(hours=12),
            date_retour_prevue=self.contract.date_retour_prevue + timedelta(days=1),
            prix_par_jour=Decimal("300"),
            statut="PENDING",
        )
        res = self.api.post(f"/api/reservations/{reservation.id}/confirm/")
        self.assertEqual(res.status_code, 400, res.content)
        reservation.refresh_from_db()
        self.assertEqual(reservation.statut, "PENDING")


class PricingQueryCountTests(TestCase):
    def test_period_price_uses_constant_number_of_queries(self):
        _, _, contract = setup_agency("Q")
        vehicle = contract.vehicle
        today = timezone.localdate()
        VehiclePriceInterval.objects.create(
            vehicle=vehicle,
            type="ABSOLUTE",
            date_debut=today,
            date_fin=today + timedelta(days=100),
            prix=Decimal("400"),
        )
        with self.assertNumQueries(1):
            quote = vehicle.prix_pour_periode(today, today + timedelta(days=29))
        self.assertEqual(quote["jours"], 30)
        self.assertEqual(quote["total"], Decimal("12000.00"))
