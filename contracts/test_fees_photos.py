import io
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from agency.models import CustomUser
from payments.test_invoicing import setup_agency


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(buf, format="PNG")
    return buf.getvalue()


class ContractFeeTests(TestCase):
    def setUp(self):
        self.ag, self.owner, self.contract = setup_agency("F")
        self.api = APIClient()
        self.api.force_authenticate(self.owner)
        self.contract.refresh_from_db()

    def _fee(self, amount, kind="RETARD"):
        return self.api.post(
            "/api/contract-fees/",
            {"contract": self.contract.id, "kind": kind, "amount": str(amount)},
            format="json",
        )

    def test_fee_increases_remaining_balance(self):
        before = self.contract.reste_a_payer
        total_before = self.contract.montant_total
        res = self._fee("150.00")
        self.assertEqual(res.status_code, 201, res.content)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.frais_supplementaires, Decimal("150.00"))
        self.assertEqual(self.contract.reste_a_payer, before + Decimal("150.00"))
        # montant_total (location) n'est pas touché
        self.assertEqual(self.contract.montant_total, total_before)

    def test_payment_can_cover_fees(self):
        self._fee("100.00")
        self.contract.refresh_from_db()
        res = self.api.post(
            "/api/payments/",
            {
                "contract": self.contract.id,
                "amount": str(self.contract.reste_a_payer),
                "payment_method": "Espèce",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.reste_a_payer, Decimal("0.00"))

    def test_invalid_amount_rejected(self):
        self.assertEqual(self._fee("0").status_code, 400)
        self.assertEqual(self._fee("-5").status_code, 400)

    def test_cannot_set_fee_total_through_contract_patch(self):
        res = self.api.patch(
            f"/api/contracts/{self.contract.id}/",
            {"frais_supplementaires": "9999"},
            format="json",
        )
        self.contract.refresh_from_db()
        self.assertEqual(
            self.contract.frais_supplementaires, Decimal("0.00"), res.content
        )

    def test_only_owner_can_delete_and_balance_is_restored(self):
        fee_id = self._fee("80.00").json()["id"]
        employee = CustomUser.objects.create_user(
            "emp_f", password="pw12345", agency=self.ag, role="EMPLOYEE"
        )
        emp_api = APIClient()
        emp_api.force_authenticate(employee)
        self.assertEqual(
            emp_api.delete(f"/api/contract-fees/{fee_id}/").status_code, 403
        )

        self.assertEqual(
            self.api.delete(f"/api/contract-fees/{fee_id}/").status_code, 204
        )
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.frais_supplementaires, Decimal("0.00"))

    def test_other_agency_cannot_add_fee(self):
        _, other_owner, _ = setup_agency("G")
        other = APIClient()
        other.force_authenticate(other_owner)
        res = other.post(
            "/api/contract-fees/",
            {"contract": self.contract.id, "kind": "AUTRE", "amount": "10"},
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)


class ContractPhotoTests(TestCase):
    def setUp(self):
        self.ag, self.owner, self.contract = setup_agency("PH")
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def _upload(self, api=None, contract=None, name="a.png"):
        return (api or self.api).post(
            "/api/contract-photos/",
            {
                "contract": (contract or self.contract).id,
                "kind": "DEPART",
                "image": SimpleUploadedFile(
                    name, _png_bytes(), content_type="image/png"
                ),
            },
            format="multipart",
        )

    @override_settings(MEDIA_ROOT="E:/krini/.tmp/media_test")
    def test_upload_and_list(self):
        res = self._upload()
        self.assertEqual(res.status_code, 201, res.content)
        listing = self.api.get(f"/api/contract-photos/?contract={self.contract.id}")
        self.assertEqual(len(listing.json()["results"]), 1)

    @override_settings(MEDIA_ROOT="E:/krini/.tmp/media_test")
    def test_other_agency_cannot_see_or_upload(self):
        self._upload()
        _, other_owner, other_contract = setup_agency("PX")
        other = APIClient()
        other.force_authenticate(other_owner)
        self.assertEqual(other.get("/api/contract-photos/").json()["results"], [])
        self.assertEqual(
            self._upload(api=other, contract=self.contract).status_code, 400
        )

    @override_settings(MEDIA_ROOT="E:/krini/.tmp/media_test")
    def test_non_image_rejected(self):
        res = self.api.post(
            "/api/contract-photos/",
            {
                "contract": self.contract.id,
                "kind": "DEPART",
                "image": SimpleUploadedFile(
                    "x.txt", b"hello", content_type="text/plain"
                ),
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 400)
