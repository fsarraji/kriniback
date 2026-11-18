from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from agency.models import Agency
from clients.models import Client
from fleet.models import Vehicle


def _agency(name):
    return Agency.objects.create(nom_agence=name, adresse="X", telephone="0600000000")


def _vehicle(agency, matricule, definitif=None):
    return Vehicle.objects.create(
        agency=agency,
        matricule=matricule,
        matricule_definitif=definitif,
        annee=2020,
        couleur="Blanc",
        carburant="Diesel",
        kilometrage=1000,
        prix_par_jour=Decimal("200"),
        date_assurance=date(2030, 1, 1),
        date_visite_technique=date(2030, 1, 1),
        prochain_vidange_km=20000,
    )


class VehicleMatriculeUniquePerAgencyTests(TestCase):
    def setUp(self):
        self.a = _agency("A")
        self.b = _agency("B")

    def test_same_matricule_allowed_in_two_agencies(self):
        _vehicle(self.a, "1234-A-1", "11111-a-1")
        _vehicle(self.b, "1234-A-1", "11111-a-1")

    def test_duplicate_matricule_rejected_in_same_agency(self):
        _vehicle(self.a, "1234-A-1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            _vehicle(self.a, "1234-A-1")

    def test_duplicate_definitif_rejected_in_same_agency(self):
        _vehicle(self.a, "M1", "11111-a-1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            _vehicle(self.a, "M2", "11111-a-1")

    def test_null_definitif_not_constrained(self):
        _vehicle(self.a, "M1")
        _vehicle(self.a, "M2")


class ClientPermisUniquePerAgencyTests(TestCase):
    def setUp(self):
        self.a = _agency("A")
        self.b = _agency("B")

    def _client(self, agency, cin, permis):
        return Client.objects.create(
            agency=agency,
            nom="N",
            prenom="P",
            telephone="0600",
            cin_passport=cin,
            permis_conduite=permis,
        )

    def test_same_permis_and_cin_allowed_in_two_agencies(self):
        self._client(self.a, "AB1", "P-1")
        self._client(self.b, "AB1", "P-1")

    def test_duplicate_permis_rejected_in_same_agency(self):
        self._client(self.a, "AB1", "P-1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._client(self.a, "AB2", "P-1")

    def test_blank_or_null_permis_not_constrained(self):
        self._client(self.a, "AB1", None)
        self._client(self.a, "AB2", None)
        self._client(self.a, "AB3", "")
        self._client(self.a, "AB4", "")
