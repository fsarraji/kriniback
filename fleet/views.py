from rest_framework import viewsets, permissions, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from datetime import datetime

from .models import Vehicle, Brand, ModelCar, Evaluation
from .serializers import VehicleSerializer, BrandSerializer, ModelCarSerializer, EvaluationSerializer
from .traccar import TraccarError, TraccarNotConfigured, create_device, get_devices, update_device
from contracts.models import Contract


def _sync_traccar_device(vehicle, agency):
    """Enregistre le périphérique GPS (IMEI) du véhicule sur le serveur Traccar.

    Si le véhicule a un IMEI mais aucun ID Traccar, on crée le dispositif sur
    le serveur et on stocke l'ID retourné dans traccar_device_id pour les
    appels API (positions, historique). Si Traccar n'est pas configuré pour
    l'agence, on ne bloque pas la sauvegarde : l'IMEI est conservé pour une
    synchronisation ultérieure.
    """
    imei = (vehicle.gps_imei or '').strip()
    if not imei or vehicle.traccar_device_id:
        return

    name = vehicle.matricule or f'Véhicule {imei}'
    try:
        device = create_device(name, imei, agency=agency)
    except TraccarNotConfigured:
        return
    except TraccarError:
        # Le dispositif existe peut-être déjà (uniqueId dupliqué) : on le retrouve
        try:
            device = next((d for d in get_devices(agency=agency) if str(d.get('uniqueId', '')).strip() == imei), None)
        except (TraccarError, TraccarNotConfigured):
            device = None
    if not device:
        return

    device_id = device.get('id')
    if device_id:
        vehicle.traccar_device_id = device_id
        vehicle.save(update_fields=['traccar_device_id'])


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
        vehicle = serializer.save(agency=agency)
        _sync_traccar_device(vehicle, agency)

    def perform_update(self, serializer):
        old_imei = (serializer.instance.gps_imei or '').strip() if serializer.instance else None
        vehicle = serializer.save()
        _sync_traccar_device(vehicle, self.request.user.agency)
        # Si l'IMEI du véhicule change et qu'un dispositif Traccar est déjà lié,
        # on met à jour l'uniqueId côté serveur pour rester cohérent.
        new_imei = (vehicle.gps_imei or '').strip()
        if vehicle.traccar_device_id and old_imei and new_imei and old_imei != new_imei:
            try:
                update_device(
                    vehicle.traccar_device_id,
                    name=vehicle.matricule or f'Véhicule {new_imei}',
                    unique_id=new_imei,
                    agency=self.request.user.agency,
                )
            except (TraccarError, TraccarNotConfigured):
                pass

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
    pagination_class = None  # catalogue complet, sans pagination

    def get_queryset(self):
        # ?all=1 -> retourne toutes les marques (utilisé pour la configuration des paramètres agence)
        if self.request.query_params.get('all') == '1' or self.request.user.is_superuser:
            return Brand.objects.all().order_by('name')
        agency = self.request.user.agency
        if agency and agency.brands.exists():
            return agency.brands.all().order_by('name')
        # Aucune marque configurée => on affiche toutes les marques
        return Brand.objects.all().order_by('name')

class ModelCarViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ModelCar.objects.all().order_by('name')
    serializer_class = ModelCarSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None  # catalogue complet, sans pagination
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['brand']

class EvaluationViewSet(viewsets.ModelViewSet):
    """
    Évaluations des véhicules par les clients connectés.
    - Lecture (GET) publique : chacun peut voir les avis.
    - Création (POST) : réservée aux clients connectés, une seule évaluation par client et véhicule.
    """
    queryset = Evaluation.objects.select_related('client', 'vehicle__marque', 'vehicle__modele')
    serializer_class = EvaluationSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['vehicle']
    ordering_fields = ['created_at', 'rating']

    def get_queryset(self):
        qs = super().get_queryset()
        vehicle = self.request.query_params.get('vehicle')
        if vehicle:
            qs = qs.filter(vehicle=vehicle)
        return qs

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def partial_update(self, request, *args, **kwargs):
        obj = self.get_object()
        if getattr(request.user, 'client_profile', None) != obj.client:
            return Response({'detail': 'Vous ne pouvez modifier que votre propre évaluation.'}, status=status.HTTP_403_FORBIDDEN)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if getattr(request.user, 'client_profile', None) != obj.client:
            return Response({'detail': 'Vous ne pouvez supprimer que votre propre évaluation.'}, status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)

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