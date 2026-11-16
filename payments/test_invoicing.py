from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from agency.models import Agency, CustomUser, Subscription
from clients.models import Client
from contracts.models import Contract
from fleet.models import Brand, ModelCar, Vehicle
from payments.invoicing import split_ttc
from payments.models import DocumentSequence, Invoice


def setup_agency(name, role="OWNER"):
    ag = Agency.objects.create(
        nom_agence=name, adresse="x", telephone="0600", tva_rate=Decimal("20.00")
    )
    Subscription.objects.create(
        agency=ag,
        start_date=timezone.localdate() - timedelta(days=1),
        end_date=timezone.localdate() + timedelta(days=60),
    )
    user = CustomUser.objects.create_user(
        f"u_{name}_{role}", password="pw12345", agency=ag, role=role
    )
    brand, _ = Brand.objects.get_or_create(name="Dacia")
    model, _ = ModelCar.objects.get_or_create(brand=brand, name="Logan")
    today = timezone.localdate()
    veh = Vehicle.objects.create(
        agency=ag,
        matricule=f"{name}-1",
        marque=brand,
        modele=model,
        annee=2021,
        kilometrage=1000,
        prix_par_jour=Decimal("300"),
        prochain_vidange_km=50000,
        date_assurance=today + timedelta(days=300),
        date_visite_technique=today + timedelta(days=300),
    )
    client = Client.objects.create(
        agency=ag, nom="Alaoui", prenom="Ahmed", telephone="0611"
    )
    now = timezone.now()
    contract = Contract.objects.create(
        agency=ag,
        vehicle=veh,
        client=client,
        km_sortie=0,
        date_sortie=now,
        date_retour_prevue=now + timedelta(days=2),
        jours=2,
        prix_par_jour=Decimal("300"),
        statut="RESERVE",
    )
    return ag, user, contract


class SplitTests(TestCase):
    def test_split_ttc(self):
        ht, tva, ttc = split_ttc("1200", "20")
        self.assertEqual(
            (ht, tva, ttc), (Decimal("1000.00"), Decimal("200.00"), Decimal("1200.00"))
        )
        self.assertEqual(
            split_ttc("100", "0"),
            (Decimal("100.00"), Decimal("0.00"), Decimal("100.00")),
        )


class InvoiceFlowTests(TestCase):
    def setUp(self):
        self.ag, self.owner, self.contract = setup_agency("A")
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def _issue(self):
        return self.api.post(
            "/api/invoices/", {"contract": self.contract.id}, format="json"
        )

    def test_issue_invoice_computes_amounts_and_number(self):
        r = self._issue()
        self.assertEqual(r.status_code, 201, r.content)
        data = r.json()
        year = timezone.localdate().year
        self.assertEqual(data["number"], f"F-{year}-0001")
        self.assertEqual(Decimal(data["montant_ttc"]), Decimal("600.00"))
        self.assertEqual(Decimal(data["montant_ht"]), Decimal("500.00"))
        self.assertEqual(Decimal(data["montant_tva"]), Decimal("100.00"))

    def test_one_active_invoice_per_contract(self):
        self.assertEqual(self._issue().status_code, 201)
        self.assertEqual(self._issue().status_code, 400)

    def test_numbers_are_sequential_per_agency(self):
        other_ag, other_user, other_contract = setup_agency("B")
        self._issue()
        year = timezone.localdate().year
        self.assertEqual(
            DocumentSequence.next_number(self.ag, "INV", year), f"F-{year}-0002"
        )
        # compteur de l'autre agence indépendant
        self.assertEqual(
            DocumentSequence.next_number(other_ag, "INV", year), f"F-{year}-0001"
        )

    def test_cancelled_contract_not_invoiceable(self):
        Contract.objects.filter(pk=self.contract.pk).update(statut="ANNULE")
        self.assertEqual(self._issue().status_code, 400)

    def test_cannot_invoice_other_agency_contract(self):
        _, _, foreign = setup_agency("B")
        r = self.api.post("/api/invoices/", {"contract": foreign.id}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_invoices_immutable_and_scoped(self):
        inv_id = self._issue().json()["id"]
        self.assertEqual(self.api.delete(f"/api/invoices/{inv_id}/").status_code, 405)
        self.assertEqual(
            self.api.patch(
                f"/api/invoices/{inv_id}/", {"notes": "x"}, format="json"
            ).status_code,
            405,
        )
        other = APIClient()
        other.force_authenticate(setup_agency("C")[1])
        self.assertEqual(other.get(f"/api/invoices/{inv_id}/").status_code, 404)

    def test_client_role_forbidden(self):
        client_user = CustomUser.objects.create_user(
            "cl", password="pw12345", role="CLIENT"
        )
        api = APIClient()
        api.force_authenticate(client_user)
        self.assertEqual(api.get("/api/invoices/").status_code, 403)

    def test_partial_then_total_credit_note(self):
        inv_id = self._issue().json()["id"]
        r1 = self.api.post(
            f"/api/invoices/{inv_id}/credit-note/",
            {"amount": "200", "reason": "geste"},
            format="json",
        )
        self.assertEqual(r1.status_code, 201, r1.content)
        self.assertEqual(Invoice.objects.get(pk=inv_id).statut, "EMISE")
        # dépassement refusé
        r2 = self.api.post(
            f"/api/invoices/{inv_id}/credit-note/", {"amount": "500"}, format="json"
        )
        self.assertEqual(r2.status_code, 400)
        # avoir du solde (total par défaut) -> facture annulée
        r3 = self.api.post(f"/api/invoices/{inv_id}/credit-note/", {}, format="json")
        self.assertEqual(r3.status_code, 201)
        self.assertEqual(Decimal(r3.json()["montant_ttc"]), Decimal("400.00"))
        self.assertEqual(Invoice.objects.get(pk=inv_id).statut, "ANNULEE")
        # facture annulée : le contrat peut être refacturé
        self.assertEqual(self._issue().status_code, 201)

    def test_credit_note_requires_owner(self):
        inv_id = self._issue().json()["id"]
        emp = CustomUser.objects.create_user(
            "emp", password="pw12345", agency=self.ag, role="EMPLOYEE"
        )
        api = APIClient()
        api.force_authenticate(emp)
        r = api.post(f"/api/invoices/{inv_id}/credit-note/", {}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_pdf_and_export(self):
        inv_id = self._issue().json()["id"]
        with mock.patch(
            "contracts.views.generate_pdf", return_value=b"%PDF-1.4 fake"
        ) as gen:
            r = self.api.get(f"/api/invoices/{inv_id}/pdf/")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r["Content-Type"], "application/pdf")
            html = gen.call_args[0][0]
            self.assertIn("FACTURE", html)
            self.assertIn("600.00", html)
        self.api.post(
            f"/api/invoices/{inv_id}/credit-note/", {"amount": "100"}, format="json"
        )
        csv_resp = self.api.get("/api/invoices/export/")
        self.assertEqual(csv_resp.status_code, 200)
        text = csv_resp.content.decode("utf-8-sig")
        lines = [l for l in text.splitlines() if l]
        self.assertEqual(len(lines), 3)  # entête + facture + avoir
        self.assertIn("-100.00", lines[2])
