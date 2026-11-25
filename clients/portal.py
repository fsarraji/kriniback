"""Espace client : lecture de ses propres contrats (le profil est dans ClientAccountView).

Un compte CLIENT n'appartient à aucune agence (user.agency est vide) : les vues
génériques filtrent par agence et ne lui renvoient donc rien. Ces vues filtrent
par le profil client lié au compte, et ne renvoient que des champs destinés au client.
"""

from rest_framework import permissions, serializers
from rest_framework.generics import ListAPIView, RetrieveAPIView

from contracts.models import Contract


def _profile_of(user):
    if not user or not user.is_authenticated or user.role != "CLIENT":
        return None
    return getattr(user, "client_profile", None)


class ClientContractSerializer(serializers.ModelSerializer):
    """Vue client d'un contrat : pas de notes internes, pas de données d'autres clients."""

    vehicle_name = serializers.SerializerMethodField()
    vehicle_matricule = serializers.CharField(
        source="vehicle.matricule", read_only=True
    )
    agency_name = serializers.CharField(source="agency.nom_agence", read_only=True)
    agency_telephone = serializers.CharField(source="agency.telephone", read_only=True)
    agency_adresse = serializers.CharField(source="agency.adresse", read_only=True)

    class Meta:
        model = Contract
        fields = [
            "id",
            "statut",
            "date_sortie",
            "date_retour_prevue",
            "date_retour_effective",
            "jours",
            "prix_par_jour",
            "montant_total",
            "montant_paye",
            "reste_a_payer",
            "km_sortie",
            "km_retour",
            "vehicle_name",
            "vehicle_matricule",
            "agency_name",
            "agency_telephone",
            "agency_adresse",
        ]
        read_only_fields = fields

    def get_vehicle_name(self, obj):
        # display_name évite la répétition de la marque (le modèle la contient déjà)
        return obj.vehicle.display_name


class _ClientContractsMixin:
    serializer_class = ClientContractSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        profile = _profile_of(self.request.user)
        if profile is None:
            return Contract.objects.none()
        return (
            Contract.objects.filter(client=profile)
            .select_related("vehicle__marque", "vehicle__modele", "agency")
            .order_by("-date_sortie")
        )


class ClientContractListView(_ClientContractsMixin, ListAPIView):
    """GET /api/clients/me/contracts/ : mes contrats (du plus récent au plus ancien)."""


class ClientContractDetailView(_ClientContractsMixin, RetrieveAPIView):
    """GET /api/clients/me/contracts/<id>/ : détail d'un de mes contrats (404 sinon)."""
