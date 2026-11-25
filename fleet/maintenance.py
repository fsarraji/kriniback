"""Entretien des véhicules : serializer + viewset."""
from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, serializers, viewsets

from car_rental_backend.permissions import check_same_agency

from .models import MaintenanceRecord


class MaintenanceRecordSerializer(serializers.ModelSerializer):
    vehicle_matricule = serializers.CharField(
        source="vehicle.matricule", read_only=True
    )
    type_label = serializers.CharField(source="get_type_display", read_only=True)
    # Crée la dépense correspondante (catégorie « Maintenance ») si coût > 0.
    create_expense = serializers.BooleanField(
        write_only=True, required=False, default=False
    )

    class Meta:
        model = MaintenanceRecord
        fields = [
            "id",
            "vehicle",
            "vehicle_matricule",
            "type",
            "type_label",
            "date",
            "km",
            "cost",
            "prochain_km",
            "prochaine_date",
            "notes",
            "expense",
            "create_expense",
            "created_at",
        ]
        read_only_fields = ["id", "expense", "created_at"]

    def validate_vehicle(self, vehicle):
        check_same_agency(self.context.get("request"), vehicle, "vehicle")
        return vehicle

    def validate_cost(self, value):
        if value < 0:
            raise serializers.ValidationError("Le coût ne peut pas être négatif.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        make_expense = validated_data.pop("create_expense", False)
        record = super().create(validated_data)
        vehicle = record.vehicle

        # Vidange : met à jour le prochain seuil kilométrique du véhicule.
        if record.type == "VIDANGE" and record.prochain_km:
            vehicle.prochain_vidange_km = record.prochain_km
            vehicle.save(update_fields=["prochain_vidange_km"])

        if make_expense and record.cost > 0:
            from expenses.models import Expense

            record.expense = Expense.objects.create(
                agency=record.agency,
                vehicle=vehicle,
                user=record.created_by,
                category="Maintenance",
                title=f"{record.get_type_display()} - {vehicle.matricule}",
                amount=record.cost,
                expense_date=record.date,
            )
            record.save(update_fields=["expense"])
        return record


class MaintenanceRecordViewSet(viewsets.ModelViewSet):
    serializer_class = MaintenanceRecordSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["vehicle", "type"]
    search_fields = ["vehicle__matricule", "notes"]
    ordering_fields = ["date", "cost", "id"]

    def get_queryset(self):
        qs = MaintenanceRecord.objects.select_related("vehicle")
        if self.request.user.is_superuser:
            return qs
        return qs.filter(agency=self.request.user.agency)

    def perform_create(self, serializer):
        agency = self.request.user.agency
        if not agency:
            raise serializers.ValidationError(
                {"detail": "Compte non lié à une agence."}
            )
        serializer.save(agency=agency, created_by=self.request.user)
