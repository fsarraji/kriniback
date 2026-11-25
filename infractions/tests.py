import os
import shutil
import tempfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from payments.test_invoicing import setup_agency

from .matching import normalize_plate
from .models import Infraction

# Stockage local jetable : ces tests ne doivent JAMAIS écrire sur le S3 de production,
# même si les clés AWS_* sont présentes dans l'environnement.
_TMP = tempfile.mkdtemp(prefix="infractions_tests_")
LOCAL_STORAGE = override_settings(
    MEDIA_ROOT=_TMP,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
)


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


def pdf(name="signe.pdf", body=b"%PDF-1.4 contrat signe"):
    return SimpleUploadedFile(name, body)


@LOCAL_STORAGE
class MatchingTests(TestCase):
    def setUp(self):
        self.ag, self.owner, self.contract = setup_agency("IN")
        self.contract.statut = "EN_COURS"
        self.contract.save()
        self.vehicle = self.contract.vehicle
        self.api = APIClient()
        self.api.force_authenticate(self.owner)
        self._complete_client()

    def _complete_client(self):
        c = self.contract.client
        c.nom_ar, c.prenom_ar, c.adresse_ar = "العلوي", "أحمد", "الدار البيضاء"
        c.type_document, c.cin_passport, c.permis_conduite = (
            "CIN",
            "AB123456",
            "01/123456",
        )
        c.adresse = "12 rue X"
        c.localite_narsa = "Casablanca - CASABLANCA PRINCIPAL (20001)"
        c.save()

    def _create(self, matricule=None, when=None, pv="PV1"):
        return self.api.post(
            "/api/infractions/",
            {
                "reference_pv": pv,
                "matricule": matricule or self.vehicle.matricule,
                "date_infraction": (
                    when or self.contract.date_sortie + timedelta(hours=5)
                ).isoformat(),
            },
            format="json",
        )

    def _queue(self, pk, file=True, api=None):
        data = {"file": pdf()} if file else {}
        return (api or self.api).post(
            f"/api/infractions/{pk}/queue/", data, format="multipart"
        )

    # --- rapprochement -------------------------------------------------

    def test_normalize_plate(self):
        self.assertEqual(normalize_plate("12345 - a - 6"), "12345A6")

    def test_matches_single_contract(self):
        res = self._create()
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["status"], Infraction.MATCHED)
        self.assertEqual(res.json()["client"], self.contract.client_id)

    def test_plate_formatting_tolerated(self):
        res = self._create(matricule=self.vehicle.matricule.lower().replace("-", " "))
        self.assertEqual(res.json()["status"], Infraction.MATCHED)

    def test_date_outside_contract(self):
        res = self._create(
            when=self.contract.date_sortie - timedelta(days=30), pv="PV2"
        )
        self.assertEqual(res.json()["status"], Infraction.NO_CONTRACT)

    def test_unknown_plate(self):
        res = self._create(matricule="ZZZ-999", pv="PV3")
        self.assertEqual(res.json()["status"], Infraction.NO_CONTRACT)

    def test_cancelled_contract_ignored(self):
        self.contract.statut = "ANNULE"
        self.contract.save()
        self.assertEqual(
            self._create(pv="PV4").json()["status"], Infraction.NO_CONTRACT
        )

    def test_candidates_and_manual_contract_pick(self):
        unknown = self._create(matricule="ZZZ-999", pv="PVC").json()["id"]
        self.assertEqual(
            self.api.get(f"/api/infractions/{unknown}/candidates/").json(), []
        )
        old = self.contract.date_sortie - timedelta(days=40)
        pk = self._create(when=old, pv="PVD").json()["id"]
        rows = self.api.get(f"/api/infractions/{pk}/candidates/").json()
        self.assertEqual(
            [(r["id"], r["covers"]) for r in rows], [(self.contract.id, False)]
        )
        res = self.api.patch(
            f"/api/infractions/{pk}/", {"contract": self.contract.id}, format="json"
        )
        self.assertEqual(res.json()["status"], "matched")

    # --- file d'attente / worker ---------------------------------------

    def test_queue_worker_result_flow(self):
        pk = self._create().json()["id"]
        self.assertEqual(self.api.get("/api/infractions/worker-queue/").json(), [])
        self.assertEqual(self._queue(pk).status_code, 200)
        queue = self.api.get("/api/infractions/worker-queue/").json()
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["client"]["nom"], "Alaoui")
        res = self.api.post(
            f"/api/infractions/{pk}/result/", {"ok": True}, format="json"
        )
        self.assertEqual(res.json()["status"], Infraction.SUBMITTED)
        # un second résultat est refusé (idempotence)
        res = self.api.post(
            f"/api/infractions/{pk}/result/", {"ok": True}, format="json"
        )
        self.assertEqual(res.status_code, 409)

    def test_cannot_queue_unmatched(self):
        pk = self._create(matricule="ZZZ-999", pv="PV5").json()["id"]
        self.assertEqual(self._queue(pk).status_code, 400)

    def test_other_agency_isolated(self):
        pk = self._create().json()["id"]
        _, other_owner, _ = setup_agency("OT")
        other = APIClient()
        other.force_authenticate(other_owner)
        self.assertEqual(other.get("/api/infractions/").json()["results"], [])
        self.assertEqual(self._queue(pk, api=other).status_code, 404)

    def test_missing_client_fields_block_queue(self):
        c = self.contract.client
        c.nom_ar = ""
        c.save()
        pk = self._create().json()["id"]
        res = self._queue(pk)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["missing"], ["nom_ar"])
        self.assertEqual(
            self.api.get(f"/api/infractions/{pk}/").json()["narsa_missing"], ["nom_ar"]
        )

    def test_address_shorter_than_five_characters_is_flagged(self):
        c = self.contract.client
        c.adresse = "adr"
        c.save()
        pk = self._create().json()["id"]
        res = self._queue(pk)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["missing"], ["adresse"])
        c.adresse = "12345"
        c.save()
        self.assertEqual(self._queue(pk).status_code, 200)

    def test_plate_format(self):
        from .narsa import narsa_plate

        self.assertEqual(narsa_plate("37013-A-49"), "37013 - أ - 49")
        self.assertEqual(narsa_plate("37013 - أ - 49"), "37013 - أ - 49")
        self.assertIsNone(narsa_plate("WW-1234"))

    # --- contrat signé : fourni à la déclaration, supprimé après envoi ---

    def test_signed_contract_required_at_queue_time(self):
        pk = self._create().json()["id"]
        res = self._queue(pk, file=False)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["missing"], ["contrat_signe"])
        self.assertEqual(Infraction.objects.get(pk=pk).status, Infraction.MATCHED)

    def test_bad_file_rejected(self):
        pk = self._create().json()["id"]
        res = self.api.post(
            f"/api/infractions/{pk}/queue/",
            {"file": pdf("faux.pdf", b"pas un pdf")},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400)
        res = self.api.post(
            f"/api/infractions/{pk}/queue/",
            {"file": pdf("x.exe", b"MZ")},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Infraction.objects.get(pk=pk).status, Infraction.MATCHED)

    def test_worker_payload_has_signed_contract_url_and_fields(self):
        pk = self._create().json()["id"]
        self._queue(pk)
        item = self.api.get("/api/infractions/worker-queue/").json()[0]
        self.assertTrue(item["signed_contract_url"].endswith(".pdf"))
        self.assertEqual(item["client"]["nom_ar"], "العلوي")
        self.assertTrue(item["client"]["localite_narsa"].endswith("(20001)"))

    def test_file_deleted_after_successful_declaration(self):
        pk = self._create().json()["id"]
        self._queue(pk)
        inf = Infraction.objects.get(pk=pk)
        path = inf.signed_contract.path
        self.assertTrue(os.path.exists(path))
        self.api.post(f"/api/infractions/{pk}/result/", {"ok": True}, format="json")
        inf.refresh_from_db()
        self.assertFalse(inf.signed_contract)
        self.assertFalse(os.path.exists(path))
        self.assertFalse(
            self.api.get(f"/api/infractions/{pk}/").json()["has_signed_contract"]
        )

    def test_file_kept_after_failure_so_retry_needs_no_reupload(self):
        pk = self._create().json()["id"]
        self._queue(pk)
        self.api.post(
            f"/api/infractions/{pk}/result/", {"ok": False, "error": "x"}, format="json"
        )
        inf = Infraction.objects.get(pk=pk)
        self.assertEqual(inf.status, Infraction.FAILED)
        self.assertTrue(inf.signed_contract)
        self.assertEqual(
            self._queue(pk, file=False).status_code, 200
        )  # relance sans nouveau fichier

    def test_file_deleted_with_infraction(self):
        pk = self._create().json()["id"]
        self._queue(pk)
        path = Infraction.objects.get(pk=pk).signed_contract.path
        self.assertEqual(self.api.delete(f"/api/infractions/{pk}/").status_code, 204)
        self.assertFalse(os.path.exists(path))
