from django.test import TestCase
from django.utils import timezone


class NaiveDatetimeNormalizationTests(TestCase):
    """Les datetimes naïfs envoyés par les formulaires agence (datetime-local)
    sont rendus timezone-aware à l'enregistrement (plus de RuntimeWarning)."""

    def setUp(self):
        from datetime import date
        from decimal import Decimal

        from agency.models import Agency
        from fleet.models import Brand, ModelCar, Vehicle

        self.agency = Agency.objects.create(
            nom_agence="Agence Test", adresse="x", telephone="0612345678"
        )
        brand = Brand.objects.create(name="Dacia")
        modele = ModelCar.objects.create(brand=brand, name="Sandero")
        self.vehicle = Vehicle.objects.create(
            agency=self.agency,
            matricule="TEST-001",
            marque=brand,
            modele=modele,
            annee=2020,
            couleur="Blanc",
            carburant="Diesel",
            kilometrage=1000,
            prix_par_jour=Decimal("250.00"),
            date_assurance=date(2026, 1, 1),
            date_visite_technique=date(2026, 1, 1),
            prochain_vidange_km=50000,
        )

    def test_reservation_serializer_normalise(self):
        from datetime import datetime
        from datetime import timezone as dt_tz

        from contracts.serializers import ReservationSerializer

        s = ReservationSerializer(
            data={
                "vehicle": self.vehicle.id,
                "date_sortie": "2026-09-14T21:50",
                "date_retour_prevue": "2026-09-19T22:50",
            }
        )
        self.assertTrue(s.is_valid(), s.errors)
        self.assertTrue(timezone.is_aware(s.validated_data["date_sortie"]))
        self.assertTrue(timezone.is_aware(s.validated_data["date_retour_prevue"]))
        self.assertEqual(
            s.validated_data["date_sortie"],
            datetime(2026, 9, 14, 21, 50, tzinfo=dt_tz.utc),
        )

    def test_contract_serializer_normalise(self):
        from datetime import datetime
        from datetime import timezone as dt_tz

        from contracts.serializers import ContractSerializer

        s = ContractSerializer(
            data={
                "vehicle": self.vehicle.id,
                "date_sortie": "2026-09-14T21:50",
                "date_retour_prevue": "2026-09-19T22:50",
            },
            partial=True,
        )
        self.assertTrue(s.is_valid(), s.errors)
        self.assertTrue(timezone.is_aware(s.validated_data["date_sortie"]))
        self.assertTrue(timezone.is_aware(s.validated_data["date_retour_prevue"]))
        self.assertEqual(
            s.validated_data["date_sortie"],
            datetime(2026, 9, 14, 21, 50, tzinfo=dt_tz.utc),
        )

    def test_reservation_api_stores_aware_datetime(self):
        from rest_framework.test import APIClient

        from agency.models import CustomUser
        from clients.models import Client
        from contracts.models import Reservation

        user = CustomUser.objects.create_user(
            username="testclient",
            password="pass12345",
            role="CLIENT",
            agency=self.agency,
        )
        Client.objects.create(
            agency=self.agency,
            user=user,
            nom="Alaoui",
            prenom="Ahmed",
            telephone="0612345678",
        )

        c = APIClient()
        c.force_authenticate(user=user)
        res = c.post(
            "/api/reservations/",
            {
                "vehicle": self.vehicle.id,
                "date_sortie": "2026-09-14T21:50",
                "date_retour_prevue": "2026-09-19T22:50",
            },
            format="json",
        )

        self.assertEqual(res.status_code, 201, res.data)
        reservation = Reservation.objects.get(pk=res.data["id"])
        self.assertTrue(timezone.is_aware(reservation.date_sortie))
        self.assertTrue(timezone.is_aware(reservation.date_retour_prevue))


class ContractLifecycleTests(TestCase):
    """return_vehicle et prolong sur les vrais modèles."""

    def setUp(self):
        from datetime import date, timedelta
        from decimal import Decimal

        from rest_framework.test import APIClient

        from agency.models import Agency, CustomUser, Subscription
        from clients.models import Client
        from contracts.models import Contract
        from fleet.models import Brand, ModelCar, Vehicle

        self.agency = Agency.objects.create(
            nom_agence="Agence L", adresse="x", telephone="0612345678"
        )
        Subscription.objects.create(
            agency=self.agency,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + timedelta(days=60),
        )
        brand = Brand.objects.create(name="Renault")
        modele = ModelCar.objects.create(brand=brand, name="Clio")
        self.vehicle = Vehicle.objects.create(
            agency=self.agency,
            matricule="L-001",
            marque=brand,
            modele=modele,
            annee=2021,
            kilometrage=1000,
            prix_par_jour=Decimal("300"),
            date_assurance=date.today() + timedelta(days=300),
            date_visite_technique=date.today() + timedelta(days=300),
            prochain_vidange_km=50000,
        )
        self.client_obj = Client.objects.create(
            agency=self.agency, nom="A", prenom="B", telephone="0600000001"
        )
        self.user = CustomUser.objects.create_user(
            "owner_l", password="pw12345", agency=self.agency, role="OWNER"
        )
        now = timezone.now()
        self.contract = Contract.objects.create(
            agency=self.agency,
            vehicle=self.vehicle,
            client=self.client_obj,
            km_sortie=1000,
            date_sortie=now - timedelta(days=2),
            date_retour_prevue=now + timedelta(days=1),
            jours=3,
            prix_par_jour=Decimal("300"),
            statut="EN_COURS",
        )
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_return_vehicle_closes_contract_and_frees_vehicle(self):
        r = self.api.post(
            f"/api/contracts/{self.contract.id}/return_vehicle/",
            {"km_retour": 1400, "carburant_retour": "4/8", "payment_amount": "300"},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.contract.refresh_from_db()
        self.vehicle.refresh_from_db()
        self.assertEqual(self.contract.statut, "TERMINE")
        self.assertEqual(self.vehicle.statut, "Available")
        self.assertEqual(self.vehicle.kilometrage, 1400)
        self.assertEqual(self.contract.montant_paye, 300)

    def test_return_vehicle_rejects_bad_payment_and_rolls_back(self):
        r = self.api.post(
            f"/api/contracts/{self.contract.id}/return_vehicle/",
            {"km_retour": 1400, "payment_amount": "abc"},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.statut, "EN_COURS")
        self.assertIsNone(self.contract.km_retour)

    def test_return_vehicle_km_lower_than_departure(self):
        r = self.api.post(
            f"/api/contracts/{self.contract.id}/return_vehicle/",
            {"km_retour": 10},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_prolong_extends_and_recomputes(self):
        from datetime import timedelta

        new_end = (self.contract.date_retour_prevue + timedelta(days=2)).isoformat()
        r = self.api.post(
            f"/api/contracts/{self.contract.id}/prolong/",
            {"date_retour_prevue": new_end},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.jours, 5)
        self.assertEqual(self.contract.montant_total, 1500)

    def test_prolong_rejects_earlier_date(self):
        from datetime import timedelta

        early = (self.contract.date_retour_prevue - timedelta(hours=5)).isoformat()
        r = self.api.post(
            f"/api/contracts/{self.contract.id}/prolong/",
            {"date_retour_prevue": early},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_closed_contract_amount_not_rewritten_by_price_change(self):
        from decimal import Decimal

        self.api.post(
            f"/api/contracts/{self.contract.id}/return_vehicle/",
            {"km_retour": 1200},
            format="json",
        )
        self.contract.refresh_from_db()
        total = self.contract.montant_total
        self.vehicle.prix_par_jour = Decimal("999")
        self.vehicle.save()
        self.contract.refresh_from_db()
        self.contract.save()
        self.assertEqual(self.contract.montant_total, total)

    def test_pdf_worker_claims_pending_and_recovers_stale(self):
        from datetime import timedelta

        from contracts.management.commands.process_pdf_jobs import claim_next_job
        from contracts.models import PdfJob

        job = PdfJob.objects.create(contract=self.contract, job_type="contract")
        self.assertEqual(claim_next_job(timedelta(minutes=10)), job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, "PROCESSING")
        # Pas repris tant qu'il n'est pas périmé
        self.assertIsNone(claim_next_job(timedelta(minutes=10)))
        PdfJob.objects.filter(pk=job.pk).update(
            updated_at=timezone.now() - timedelta(minutes=30)
        )
        self.assertEqual(claim_next_job(timedelta(minutes=10)), job.id)
