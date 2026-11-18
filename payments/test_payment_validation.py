from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from payments.test_invoicing import setup_agency


class PaymentValidationTests(TestCase):
    def setUp(self):
        self.ag, self.user, self.contract = setup_agency("V")
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.contract.refresh_from_db()

    def _pay(self, amount):
        return self.api.post(
            "/api/payments/",
            {
                "contract": self.contract.id,
                "amount": str(amount),
                "payment_method": "Espèce",
            },
            format="json",
        )

    def test_zero_amount_rejected(self):
        self.assertEqual(self._pay(0).status_code, 400)

    def test_overpayment_rejected(self):
        too_much = self.contract.reste_a_payer + Decimal("1")
        res = self._pay(too_much)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("amount", res.json())

    def test_exact_payment_then_refund_limits(self):
        self.assertEqual(self._pay(self.contract.reste_a_payer).status_code, 201)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.reste_a_payer, Decimal("0.00"))
        # Remboursement partiel autorisé, supérieur au payé refusé
        self.assertEqual(self._pay(Decimal("-100")).status_code, 201)
        self.contract.refresh_from_db()
        self.assertEqual(self._pay(-(self.contract.montant_paye + 1)).status_code, 400)
