from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from car_rental_backend.permissions import IsAgencyStaff
from contracts.views import _AgencyScopedMixin

from .matching import contracts_covering, find_vehicle, match_infraction
from .models import Infraction
from .narsa import narsa_missing_fields
from .serializers import (
    CandidateContractSerializer,
    InfractionSerializer,
    WorkerPayloadSerializer,
)


class InfractionViewSet(_AgencyScopedMixin, viewsets.ModelViewSet):
    queryset = Infraction.objects.select_related("client", "contract", "vehicle")
    serializer_class = InfractionSerializer
    permission_classes = [IsAgencyStaff]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["status", "matricule"]

    def perform_create(self, serializer):
        obj = serializer.save(agency=self._agency())
        if obj.contract_id:  # choix manuel du contrat
            obj.vehicle, obj.client = obj.contract.vehicle, obj.contract.client
            obj.status = Infraction.MATCHED
            obj.save()
        else:
            match_infraction(obj)

    def perform_update(self, serializer):
        obj = serializer.save()
        if obj.contract_id:
            obj.vehicle, obj.client = obj.contract.vehicle, obj.contract.client
            obj.status = Infraction.MATCHED
            obj.error = ""
            obj.save()
        else:
            match_infraction(obj)

    @action(
        detail=True,
        methods=["post"],
        parser_classes=[MultiPartParser, FormParser, JSONParser],
    )
    def queue(self, request, pk=None):
        """Met en file pour le worker. Le contrat signé (champ `file`) est exigé par NARSA ;
        il n'est gardé que le temps de la déclaration puis supprimé."""
        from django.core.exceptions import ValidationError as DjangoValidationError

        from car_rental_backend.validators import validate_signed_contract

        obj = self.get_object()
        if (
            obj.status not in (Infraction.MATCHED, Infraction.FAILED)
            or not obj.client_id
        ):
            raise ValidationError({"detail": "Rapprocher d'abord un contrat/client."})
        missing = narsa_missing_fields(obj.client)
        if missing:
            raise ValidationError(
                {
                    "detail": "Données NARSA manquantes chez le client.",
                    "missing": missing,
                }
            )
        upload = request.FILES.get("file")
        if upload is not None:
            try:
                validate_signed_contract(upload)
            except DjangoValidationError as exc:
                raise ValidationError({"file": exc.messages})
            obj.discard_signed_contract()
            obj.signed_contract.save(upload.name, upload, save=False)
        elif not obj.signed_contract:
            raise ValidationError(
                {
                    "detail": "Contrat signé requis (champ `file`).",
                    "missing": ["contrat_signe"],
                }
            )
        obj.status = Infraction.QUEUED
        obj.error = ""
        obj.save(update_fields=["status", "error", "signed_contract"])
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["get"])
    def candidates(self, request, pk=None):
        """Contrats de la voiture concernée (en cours ou passés) pour choisir le bon locataire."""
        from contracts.models import Contract

        obj = self.get_object()
        vehicle = obj.vehicle or find_vehicle(obj.agency, obj.matricule)
        if vehicle is None:
            return Response([])
        covering = {c.id for c in contracts_covering(vehicle, obj.date_infraction)}
        rows = (
            Contract.objects.filter(vehicle=vehicle)
            .exclude(statut="ANNULE")
            .select_related("client")
            .order_by("-date_sortie")[:30]
        )
        data = [
            {
                "id": c.id,
                "client_nom": f"{c.client.nom} {c.client.prenom}".strip()
                if c.client
                else "",
                "date_sortie": c.date_sortie,
                "date_retour": c.date_retour_effective or c.date_retour_prevue,
                "statut": c.statut,
                "covers": c.id in covering,
            }
            for c in rows
        ]
        return Response(CandidateContractSerializer(data, many=True).data)

    @action(detail=False, methods=["get"], url_path="worker-queue")
    def worker_queue(self, request):
        qs = (
            self.get_queryset()
            .filter(status=Infraction.QUEUED)
            .select_related("client")
        )
        return Response(
            WorkerPayloadSerializer(
                qs[:20], many=True, context={"request": request}
            ).data
        )

    @action(detail=True, methods=["post"])
    def result(self, request, pk=None):
        """Résultat du worker : {"ok": true} ou {"ok": false, "error": "..."}."""
        with transaction.atomic():
            obj = self.get_queryset().select_for_update().get(pk=pk)
            if obj.status != Infraction.QUEUED:
                return Response(
                    {"detail": "Infraction non en file."},
                    status=status.HTTP_409_CONFLICT,
                )
            if request.data.get("ok") is True:
                obj.mark_submitted()
            else:
                obj.status = Infraction.FAILED
                obj.error = str(request.data.get("error", ""))[:1000]
                obj.save(update_fields=["status", "error"])
        return Response(self.get_serializer(obj).data)
