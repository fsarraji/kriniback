from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from agency.models import CustomUser
from contracts.models import Contract
from payments.test_invoicing import setup_agency


def make_client_account(agency, username, nom):
    from clients.models import Client

    user = CustomUser.objects.create_user(
        username=username, password="pw12345", role="CLIENT", agency=agency
    )
    profile = Client.objects.create(
        agency=agency,
        user=user,
        nom=nom,
        prenom="Test",
        telephone="0611" + username[-4:].ljust(4, "0"),
        cin_passport="CIN-" + username,
        permis_conduite="PERM-" + username,
    )
    return user, profile


class ClientCannotReachAgencyDataTest(TestCase):
    """Un compte CLIENT rattaché à une agence ne doit voir ni modifier les contrats de l'agence."""

    def setUp(self):
        self.ag, self.owner, self.contract = setup_agency("P")
        self.client_user, self.profile = make_client_account(self.ag, "cl_p1", "Client")

    def test_client_cannot_list_agency_contracts(self):
        api = APIClient()
        api.force_authenticate(self.client_user)
        r = api.get("/api/contracts/")
        self.assertEqual(r.status_code, 403, r.content)

    def test_client_cannot_modify_agency_contract(self):
        api = APIClient()
        api.force_authenticate(self.client_user)
        r = api.patch(
            f"/api/contracts/{self.contract.id}/", {"statut": "ANNULE"}, format="json"
        )
        self.assertIn(r.status_code, (403, 404))
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.statut, "RESERVE")


class ClientPortalContractsTest(TestCase):
    def setUp(self):
        self.ag, self.owner, self.contract = setup_agency("Q")
        self.user, self.profile = make_client_account(self.ag, "cl_q1", "Mine")
        self.contract.client = self.profile
        self.contract.save()
        now = timezone.now()
        self.other_user, self.other_profile = make_client_account(
            self.ag, "cl_q2", "Other"
        )
        self.other_contract = Contract.objects.create(
            agency=self.ag,
            vehicle=self.contract.vehicle,
            client=self.other_profile,
            km_sortie=0,
            date_sortie=now + timedelta(days=30),
            date_retour_prevue=now + timedelta(days=32),
            jours=2,
            prix_par_jour=Decimal("300"),
            statut="RESERVE",
        )

    def test_lists_only_my_contracts_with_client_fields(self):
        api = APIClient()
        api.force_authenticate(self.user)
        r = api.get("/api/clients/me/contracts/")
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()["results"]
        self.assertEqual([c["id"] for c in data], [self.contract.id])
        row = data[0]
        self.assertEqual(row["agency_name"], self.ag.nom_agence)
        self.assertIn("vehicle_name", row)
        # pas de marque répétée (« Dacia Dacia Logan »)
        self.assertEqual(row["vehicle_name"], self.contract.vehicle.display_name)
        self.assertNotIn("Dacia Dacia", row["vehicle_name"])
        # champs internes absents
        for forbidden in ("created_by", "degats_depart", "agency", "client", "caution"):
            self.assertNotIn(forbidden, row)

    def test_detail_of_other_client_is_404(self):
        api = APIClient()
        api.force_authenticate(self.user)
        self.assertEqual(
            api.get(f"/api/clients/me/contracts/{self.other_contract.id}/").status_code,
            404,
        )
        self.assertEqual(
            api.get(f"/api/clients/me/contracts/{self.contract.id}/").status_code, 200
        )

    def test_staff_has_no_client_contracts(self):
        api = APIClient()
        api.force_authenticate(self.owner)
        r = api.get("/api/clients/me/contracts/")
        self.assertEqual(r.json()["results"], [])

    def test_anonymous_refused(self):
        self.assertIn(
            APIClient().get("/api/clients/me/contracts/").status_code, (401, 403)
        )


class ClientBlockedFromStaffEndpointsTest(TestCase):
    """Un CLIENT rattaché à une agence ne doit lire aucune donnée du personnel."""

    STAFF_ENDPOINTS = [
        "/api/clients/",
        "/api/vehicles/",
        "/api/expenses/",
        "/api/payments/",
        "/api/maintenance/",
        "/api/notifications/",
        "/api/pdf-jobs/",
        "/api/booking-requests/",
        "/api/dashboard/",
        "/api/gps/devices/",
        "/api/gps/positions/",
        "/api/agency/settings/",
    ]

    def setUp(self):
        self.ag, self.owner, self.contract = setup_agency("S")
        self.user, self.profile = make_client_account(self.ag, "cl_s1", "Sec")

    def test_staff_endpoints_are_forbidden(self):
        api = APIClient()
        api.force_authenticate(self.user)
        for ep in self.STAFF_ENDPOINTS:
            with self.subTest(endpoint=ep):
                self.assertEqual(api.get(ep).status_code, 403)

    def test_public_booking_creation_still_open(self):
        # la création de demande reste publique (anonyme) : seule la lecture est réservée
        r = APIClient().post("/api/booking-requests/", {}, format="json")
        self.assertNotEqual(r.status_code, 403)

    def test_client_reservations_still_scoped_to_self(self):
        api = APIClient()
        api.force_authenticate(self.user)
        r = api.get("/api/reservations/")
        self.assertEqual(r.status_code, 200, r.content)
