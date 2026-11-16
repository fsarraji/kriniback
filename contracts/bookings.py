"""Demandes de réservation publiques et réservations clients (extrait de views.py)."""
from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import BookingRequest, Reservation, _release_vehicle
from .serializers import BookingRequestSerializer, ReservationSerializer


class BookingRequestViewSet(viewsets.ModelViewSet):
    """
    Demandes de réservation des clients (leads).

    - La création (POST) est publique : les clients peuvent réserver sans compte.
    - La consultation et la gestion (GET/PATCH) sont réservées au personnel de l'agence.
    """

    serializer_class = BookingRequestSerializer
    throttle_scope = "public_write"

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["statut"]
    search_fields = ["nom", "prenom", "telephone", "email", "vehicle__matricule"]
    ordering_fields = ["created_at", "statut"]

    def get_permissions(self):
        if self.action == "create":
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def get_throttles(self):
        # Seule la création publique (anti-spam) est limitée : le personnel authentifié
        # consulte cette liste à chaque page (cloche de notifications, écran
        # Réservations).
        if self.action != "create":
            return []
        return super().get_throttles()

    def get_queryset(self):
        if self.request.user.is_superuser:
            return BookingRequest.objects.all()
        return BookingRequest.objects.filter(agency=self.request.user.agency)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def confirm(self, request, pk=None):
        """
        Confirme une demande de réservation (BookingRequest) :
        1. Crée automatiquement le client s'il n'existe pas encore dans l'agence.
        2. Crée un contrat en statut RESERVE.
        3. Marque la demande comme CONFIRMED.
        """
        booking = self.get_object()

        if booking.statut != "PENDING":
            return Response(
                {"detail": "Seule une demande en attente peut être confirmée."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not booking.vehicle:
            return Response(
                {"detail": "Aucun véhicule associé à cette demande."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        agency = booking.agency
        import math

        from clients.models import Client

        # 1. Chercher un client existant dans l'agence par téléphone
        client = Client.objects.filter(
            agency=agency, telephone=booking.telephone
        ).first()

        # 2. Si introuvable, créer automatiquement la fiche client
        if not client:
            # CIN/permis laissés vides (NULL) : à compléter par l'agence. Les
            # valeurs "TEMP-..." violaient l'unicité globale du permis.
            client = Client.objects.create(
                agency=agency,
                nom=booking.nom,
                prenom=booking.prenom,
                telephone=booking.telephone,
                email=booking.email or "",
                adresse="",
                remarques=(
                    "Créé automatiquement depuis la demande de réservation "
                    f"#{booking.id}"
                ),
            )

        # 3. Calcul de la durée
        diff = booking.date_retour_prevue - booking.date_sortie
        jours = max(1, math.ceil(diff.total_seconds() / (24 * 3600)))

        # 4. Créer le contrat en statut RESERVE
        from contracts.models import Contract

        contract = Contract.objects.create(
            agency=agency,
            created_by=request.user,
            vehicle=booking.vehicle,
            client=client,
            date_sortie=booking.date_sortie,
            date_retour_prevue=booking.date_retour_prevue,
            jours=jours,
            prix_par_jour=booking.vehicle.prix_par_jour,
            caution=agency.caution_montant if agency.caution_active else 0,
            km_sortie=booking.vehicle.kilometrage or 0,
            carburant_sortie="4/8",
            statut="RESERVE",
        )

        # 5. Marquer la demande comme CONFIRMÉE
        booking.statut = "CONFIRMED"
        booking.save(update_fields=["statut"])

        return Response(
            {
                "detail": "Demande confirmée. Client et contrat créés avec succès.",
                "contract_id": contract.id,
                "client_id": client.id,
                "client_created": client.remarques
                and "automatiquement" in client.remarques,
            },
            status=status.HTTP_201_CREATED,
        )


class ReservationViewSet(viewsets.ModelViewSet):
    """
    Réservations des clients.

    - Un client connecté peut créer sa réservation, consulter les siennes et
      annuler une réservation en attente.
    - Le personnel de l'agence voit les réservations de son agence et peut les
      confirmer (→ contrat RESERVE).
    """

    serializer_class = ReservationSerializer
    permission_classes = [permissions.IsAuthenticated]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["statut"]
    search_fields = [
        "client__nom",
        "client__prenom",
        "client__telephone",
        "vehicle__matricule",
        "vehicle__marque__name",
        "vehicle__modele__name",
    ]
    ordering_fields = ["created_at", "date_sortie", "prix_par_jour"]

    def get_queryset(self):
        if self.request.user.role == "CLIENT":
            client = getattr(self.request.user, "client_profile", None)
            if not client:
                return Reservation.objects.none()
            return Reservation.objects.filter(client=client)
        if self.request.user.is_superuser:
            return Reservation.objects.all()
        return Reservation.objects.filter(agency=self.request.user.agency)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    def partial_update(self, request, *args, **kwargs):
        reservation = self.get_object()
        # Un client ne peut que annuler sa propre réservation en attente
        if request.user.role == "CLIENT":
            if reservation.client != getattr(request.user, "client_profile", None):
                return Response(
                    {"detail": "Réservation introuvable."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if (
                reservation.statut != "PENDING"
                or request.data.get("statut") != "CANCELLED"
            ):
                return Response(
                    {"detail": "Vous ne pouvez qu'annuler une réservation en attente."},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            # Rétrocompatibilité : PATCH statut=CONFIRMED sur une réservation en attente
            # crée aussi le contrat (anciennes versions de l'app mobile).
            if (
                request.data.get("statut") == "CONFIRMED"
                and reservation.statut == "PENDING"
            ):
                return self.confirm(request)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        reservation = self.get_object()
        if request.user.role == "CLIENT":
            if reservation.client != getattr(request.user, "client_profile", None):
                return Response(
                    {"detail": "Réservation introuvable."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if reservation.statut != "PENDING":
                return Response(
                    {
                        "detail": (
                            "Vous ne pouvez supprimer qu'une réservation en attente."
                        )
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
        response = super().destroy(request, *args, **kwargs)
        _release_vehicle(reservation.vehicle)
        return response

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Annule une réservation (PENDING ou CONFIRMED).

        - Un client ne peut annuler que sa propre réservation en attente.
        - Le personnel d'agence peut annuler une réservation en attente.
        - Le gérant (OWNER) ou un superadmin peut annuler n'importe quelle
          réservation, y compris confirmée.
        """
        reservation = self.get_object()

        if request.user.role == "CLIENT":
            if reservation.client != getattr(request.user, "client_profile", None):
                return Response(
                    {"detail": "Réservation introuvable."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if reservation.statut != "PENDING":
                return Response(
                    {"detail": "Vous ne pouvez qu'annuler une réservation en attente."},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            # Annulation d'une réservation confirmée : réservée au gérant/superadmin.
            if reservation.statut == "CONFIRMED" and not (
                request.user.is_superuser or request.user.role == "OWNER"
            ):
                return Response(
                    {
                        "detail": (
                            "Seul le gérant de la société peut annuler une "
                            "réservation confirmée."
                        )
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
            if reservation.statut not in ("PENDING", "CONFIRMED"):
                return Response(
                    {"detail": "Réservation déjà annulée."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        reservation.statut = "CANCELLED"
        # Le save() du modèle libère le véhicule lorsque le statut devient CANCELLED.
        reservation.save(update_fields=["statut"])

        return Response(
            {
                "detail": "Réservation annulée avec succès.",
                "statut": reservation.statut,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        """Confirme la réservation et crée un contrat en statut RESERVE."""
        if request.user.role == "CLIENT":
            return Response(
                {"detail": "Action réservée au personnel."},
                status=status.HTTP_403_FORBIDDEN,
            )

        reservation = self.get_object()
        if reservation.statut != "PENDING":
            return Response(
                {"detail": "Seule une réservation en attente peut être confirmée."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        import math

        from contracts.models import Contract

        diff = reservation.date_retour_prevue - reservation.date_sortie
        jours = max(1, math.ceil(diff.total_seconds() / (24 * 3600)))

        contract = Contract.objects.create(
            agency=reservation.agency,
            created_by=request.user if request.user.is_authenticated else None,
            vehicle=reservation.vehicle,
            client=reservation.client,
            date_sortie=reservation.date_sortie,
            date_retour_prevue=reservation.date_retour_prevue,
            jours=jours,
            prix_par_jour=reservation.prix_par_jour,
            caution=reservation.agency.caution_montant,
            km_sortie=int(request.data.get("km_sortie", 0) or 0),
            carburant_sortie=request.data.get("carburant_sortie", "4/8"),
            statut="RESERVE",
        )

        reservation.statut = "CONFIRMED"
        reservation.save(update_fields=["statut"])

        return Response(
            {
                "detail": "Réservation confirmée, contrat créé.",
                "contract_id": contract.id,
            },
            status=status.HTTP_201_CREATED,
        )
