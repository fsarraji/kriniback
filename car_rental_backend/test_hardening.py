from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from car_rental_backend.validators import MAX_IMAGE_BYTES, validate_image_size
from contracts.availability import assert_vehicle_free
from payments.test_invoicing import setup_agency


class UploadValidatorTests(TestCase):
    def test_oversize_image_rejected(self):
        big = SimpleUploadedFile(
            "x.jpg", b"0" * (MAX_IMAGE_BYTES + 1), content_type="image/jpeg"
        )
        with self.assertRaises(DjangoValidationError):
            validate_image_size(big)

    def test_small_image_accepted(self):
        validate_image_size(
            SimpleUploadedFile("x.jpg", b"0" * 1024, content_type="image/jpeg")
        )


class AvailabilityLockTests(TestCase):
    def setUp(self):
        _, _, self.contract = setup_agency("L")
        self.vehicle = self.contract.vehicle

    def test_overlap_detected_under_lock(self):
        s = self.contract.date_sortie
        with transaction.atomic():
            with self.assertRaises(ValidationError):
                assert_vehicle_free(self.vehicle, s, s + timedelta(days=1))

    def test_free_period_ok_and_exclusion(self):
        later = self.contract.date_retour_prevue + timedelta(days=10)
        with transaction.atomic():
            assert_vehicle_free(self.vehicle, later, later + timedelta(days=1))
            # le contrat lui-même est exclu (modification)
            assert_vehicle_free(
                self.vehicle,
                self.contract.date_sortie,
                self.contract.date_retour_prevue,
                exclude_contract=self.contract,
            )

    def test_free_far_future(self):
        now = timezone.now()
        # TestCase enveloppe dans une transaction : on vérifie le chemin nominal.
        with transaction.atomic():
            assert_vehicle_free(
                self.vehicle, now + timedelta(days=90), now + timedelta(days=91)
            )


class BookingThrottleScopeTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        _, self.owner, _ = setup_agency("T")

    def test_staff_listing_is_not_throttled(self):
        from rest_framework.test import APIClient

        api = APIClient()
        api.force_authenticate(self.owner)
        # public_write = 20/heure : 30 consultations ne doivent jamais renvoyer 429
        codes = {api.get("/api/booking-requests/").status_code for _ in range(30)}
        self.assertEqual(codes, {200})

    def test_public_creation_stays_throttled(self):
        from rest_framework.test import APIClient

        anon = APIClient()
        codes = [
            anon.post("/api/booking-requests/", {}, format="json").status_code
            for _ in range(25)
        ]
        self.assertIn(429, codes)
