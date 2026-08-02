from rest_framework import viewsets, permissions, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from datetime import datetime

from .models import Vehicle, Brand, ModelCar
from .serializers import VehicleSerializer, BrandSerializer, ModelCarSerializer
from contracts.models import Contract

class VehicleViewSet(viewsets.ModelViewSet):
    serializer_class = VehicleSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['statut', 'carburant', 'marque', 'annee']
    search_fields = ['matricule', 'marque__name', 'modele__name']
    ordering_fields = ['prix_par_jour', 'kilometrage', 'annee', 'id']

    def get_queryset(self):
        base = Vehicle.objects.select_related('marque', 'modele', 'agency')
        if self.request.user.is_superuser:
            return base
        return base.filter(agency=self.request.user.agency)

    def perform_create(self, serializer):
        agency = self.request.user.agency
        if not agency:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"detail": "Votre compte n'est lié à aucune agence. Veuillez contacter l'administrateur."})
        serializer.save(agency=agency)

    @action(detail=False, methods=['get'])
    def available_cars(self, request):
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')

        if not start_date_str or not end_date_str:
            return Response({"detail": "Veuillez préciser la date de début et la date de fin (start_date, end_date)."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            start_date = datetime.fromisoformat(start_date_str)
            end_date = datetime.fromisoformat(end_date_str)
        except ValueError:
            return Response({"detail": "Format de date invalide, utilisez : YYYY-MM-DDTHH:MM"}, status=status.HTTP_400_BAD_REQUEST)

        agency = request.user.agency
        contract_filters = {
            'statut__in': ['RESERVE', 'EN_COURS'],
            'date_sortie__lt': end_date,
            'date_retour_prevue__gt': start_date
        }
        if not request.user.is_superuser:
            contract_filters['agency'] = agency
            
        overlapping_contracts = Contract.objects.filter(**contract_filters)
        reserved_vehicle_ids = overlapping_contracts.values_list('vehicle_id', flat=True)
        vehicle_queryset = Vehicle.objects.all() if request.user.is_superuser else Vehicle.objects.filter(agency=agency)
        available_vehicles = vehicle_queryset.exclude(id__in=reserved_vehicle_ids)

        serializer = self.get_serializer(available_vehicles, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

class BrandViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Brand.objects.all().order_by('name')
    serializer_class = BrandSerializer
    permission_classes = [permissions.IsAuthenticated]

class ModelCarViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ModelCar.objects.all().order_by('name')
    serializer_class = ModelCarSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['brand']

class PublicVehicleViewSet(viewsets.ReadOnlyModelViewSet):
    """
    عرض السيارات المتاحة للجميع بدون تسجيل دخول
    """
    queryset = Vehicle.objects.filter(statut='Available')
    serializer_class = VehicleSerializer
    permission_classes = [permissions.AllowAny]
    
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['carburant', 'marque', 'marque__name', 'modele__name', 'agency__nom_agence']
    search_fields = ['marque__name', 'modele__name', 'agency__nom_agence']
    ordering_fields = ['prix_par_jour', 'annee']