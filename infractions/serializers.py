from rest_framework import serializers

from .models import Infraction
from .narsa import narsa_missing_fields, narsa_plate


class InfractionSerializer(serializers.ModelSerializer):
    client_nom = serializers.SerializerMethodField()
    narsa_missing = serializers.SerializerMethodField()
    has_signed_contract = serializers.SerializerMethodField()

    class Meta:
        model = Infraction
        fields = [
            "id",
            "reference_pv",
            "matricule",
            "date_infraction",
            "lieu",
            "montant",
            "vehicle",
            "contract",
            "client",
            "client_nom",
            "narsa_missing",
            "has_signed_contract",
            "status",
            "error",
            "submitted_at",
            "created_at",
        ]
        read_only_fields = [
            "vehicle",
            "client",
            "status",
            "error",
            "submitted_at",
            "created_at",
        ]

    def get_client_nom(self, obj):
        c = obj.client
        return f"{c.nom} {c.prenom}".strip() if c else None

    def get_narsa_missing(self, obj):
        """Champs NARSA obligatoires manquants chez le client (vide = prêt à déclarer)."""
        return narsa_missing_fields(obj.client) if obj.client_id else []

    def get_has_signed_contract(self, obj):
        return bool(obj.signed_contract)

    def validate_contract(self, contract):
        agency = self.context["request"].user.agency
        if contract and contract.agency_id != getattr(agency, "id", None):
            raise serializers.ValidationError("Contrat d'une autre agence.")
        return contract


class WorkerPayloadSerializer(serializers.ModelSerializer):
    """Données dont le worker NARSA a besoin pour remplir le formulaire."""

    client = serializers.SerializerMethodField()
    plate_narsa = serializers.SerializerMethodField()
    signed_contract_url = serializers.SerializerMethodField()

    class Meta:
        model = Infraction
        fields = [
            "id",
            "reference_pv",
            "matricule",
            "plate_narsa",
            "date_infraction",
            "client",
            "signed_contract_url",
        ]

    def get_signed_contract_url(self, obj):
        f = obj.signed_contract
        if not f:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(f.url) if request else f.url

    def get_plate_narsa(self, obj):
        v = obj.vehicle
        for candidate in (
            obj.matricule,
            getattr(v, "matricule_definitif", None),
            getattr(v, "matricule", None),
        ):
            plate = narsa_plate(candidate or "")
            if plate:
                return plate
        return None

    def get_client(self, obj):
        c = obj.client
        if not c:
            return None
        return {
            "nom": c.nom,
            "prenom": c.prenom,
            "nom_ar": c.nom_ar,
            "prenom_ar": c.prenom_ar,
            "type_document": c.type_document,
            "cin_passport": c.cin_passport,
            "permis_conduite": c.permis_conduite,
            "permis_etranger": c.permis_etranger,
            "adresse": c.adresse,
            "adresse_ar": c.adresse_ar,
            "adresse_etrangere": c.adresse_etrangere,
            "localite_narsa": c.localite_narsa,
            "email": c.email,
            "telephone": c.telephone,
        }


class CandidateContractSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    client_nom = serializers.CharField(allow_blank=True)
    date_sortie = serializers.DateTimeField()
    date_retour = serializers.DateTimeField(allow_null=True)
    statut = serializers.CharField()
    covers = serializers.BooleanField()
