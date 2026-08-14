from rest_framework import viewsets, permissions, filters, status
from rest_framework.decorators import action
from rest_framework.views import APIView
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import F, Q, Sum
from datetime import datetime

from .models import Vehicle, Brand, ModelCar, Evaluation, GpsDevice
from .serializers import VehicleSerializer, BrandSerializer, ModelCarSerializer, EvaluationSerializer
from .traccar import (
    TraccarError,
    TraccarNotConfigured,
    create_device,
    get_devices,
    update_device,
    update_device_accumulators,
)
from contracts.models import Contract, Reservation


def _annotate_km_loue(qs):
    """Calcule le kilométrage loué (km parcourus dans les contrats terminés)."""
    return qs.annotate(
        km_loue=Sum(
            F('contracts__km_retour') - F('contracts__km_sortie'),
            filter=Q(
                contracts__statut='TERMINE',
                contracts__km_retour__isnull=False,
                contracts__km_retour__gte=F('contracts__km_sortie'),
            ),
        )
    )


def _sync_traccar_device(vehicle, agency):
    """Enregistre le périphérique GPS (IMEI) du véhicule sur le serveur Traccar.

    Si le véhicule a un IMEI mais aucun dispositif lié, on crée le dispositif sur
    le serveur et on enregistre le lien dans la table GpsDevice pour les appels
    API (positions, historique). Si Traccar n'est pas configuré pour l'agence, on
    ne bloque pas la sauvegarde : l'IMEI est conservé pour une synchronisation
    ultérieure.
    """
    imei = (vehicle.gps_imei or '').strip()
    if not imei or GpsDevice.objects.filter(vehicle=vehicle).exists():
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
        GpsDevice.attach(vehicle, device_id, agency, name=name, unique_id=imei)


def _push_kilometrage_to_traccar(vehicle, agency):
    """Pousse Vehicle.kilometrage (odomètre) vers le dispositif Traccar du véhicule.

    N'écrit jamais un kilométrage nul : un traceur neuf ou un véhicule créé sans
    kilométrage ne doit pas écraser l'odomètre déjà enregistré côté Traccar.
    """
    device_id = GpsDevice.objects.filter(vehicle=vehicle).values_list('traccar_device_id', flat=True).first()
    km = vehicle.kilometrage
    if not device_id or km is None or km <= 0:
        return
    try:
        update_device_accumulators(device_id, total_distance_m=km * 1000, agency=agency)
    except (TraccarError, TraccarNotConfigured):
        pass


class VehicleViewSet(viewsets.ModelViewSet):
    serializer_class = VehicleSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['statut', 'carburant', 'marque', 'annee', 'is_archived', 'is_deleted']
    search_fields = ['matricule', 'marque__name', 'modele__name']
    ordering_fields = ['prix_par_jour', 'kilometrage', 'annee', 'id']

    def get_queryset(self):
        base = _annotate_km_loue(Vehicle.objects.select_related('marque', 'modele', 'agency', 'gps_device'))
        if self.request.user.is_superuser:
            qs = base
        else:
            qs = base.filter(agency=self.request.user.agency)
        # Suppression douce : masquée pour tous sauf le super admin qui consulte les véhicules supprimés.
        if self.request.query_params.get('include_deleted') != '1' or not self.request.user.is_superuser:
            qs = qs.filter(is_deleted=False)
        # Les véhicules archivés (fin de travail) sont masqués par défaut.
        if self.request.query_params.get('include_archived') != '1':
            qs = qs.filter(is_archived=False)
        return qs

    def perform_create(self, serializer):
        agency = self.request.user.agency
        if not agency:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"detail": "Votre compte n'est lié à aucune agence. Veuillez contacter l'administrateur."})
        vehicle = serializer.save(agency=agency)
        _sync_traccar_device(vehicle, agency)
        _push_kilometrage_to_traccar(vehicle, agency)

    def perform_update(self, serializer):
        old_imei = (serializer.instance.gps_imei or '').strip() if serializer.instance else None
        old_km = serializer.instance.kilometrage if serializer.instance else None
        vehicle = serializer.save()
        _sync_traccar_device(vehicle, self.request.user.agency)
        # Le kilométrage saisi dans le formulaire est transféré vers Traccar
        # (source de vérité de l'odomètre) si un dispositif est lié au véhicule.
        if 'kilometrage' in serializer.validated_data and old_km != vehicle.kilometrage:
            _push_kilometrage_to_traccar(vehicle, self.request.user.agency)
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

    def destroy(self, request, *args, **kwargs):
        """Suppression douce : le véhicule n'est pas supprimé de la base,
        il est masqué de la flotte et pourra être restauré par le super admin."""
        obj = self.get_object()
        obj.is_deleted = True
        obj.save(update_fields=['is_deleted'])
        return Response(status=status.HTTP_204_NO_CONTENT)

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
        vehicle_queryset = _annotate_km_loue(vehicle_queryset)
        available_vehicles = vehicle_queryset.exclude(id__in=reserved_vehicle_ids).filter(is_archived=False, is_deleted=False)

        serializer = self.get_serializer(available_vehicles, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

class VehicleCheckUniqueView(APIView):
    """Vérifie en temps réel (après la saisie) si un matricule est déjà utilisé
    par un autre véhicule. `exclude_id` permet d'exclure le véhicule courant
    lors de l'édition."""
    permission_classes = [permissions.IsAuthenticated]

    ALLOWED_FIELDS = {'matricule'}

    def get(self, request):
        field = request.query_params.get('field', '')
        value = (request.query_params.get('value') or '').strip()
        exclude_id = request.query_params.get('exclude_id')

        if field not in self.ALLOWED_FIELDS or not value:
            return Response({'available': True, 'field': field, 'value': value})

        qs = Vehicle.objects.all()
        if exclude_id:
            qs = qs.exclude(pk=exclude_id)

        exists = qs.filter(**{field: value}).exists()
        return Response({
            'available': not exists,
            'field': field,
            'value': value,
        })


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
    serializer_class = VehicleSerializer
    permission_classes = [permissions.AllowAny]
    
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['carburant', 'marque', 'marque__name', 'modele__name', 'agency__nom_agence']
    search_fields = ['marque__name', 'modele__name', 'agency__nom_agence']
    ordering_fields = ['prix_par_jour', 'annee']

    def get_queryset(self):
        qs = Vehicle.objects.filter(
            is_archived=False,
            is_deleted=False,
            agency__is_active=True,
        )

        # ?all=1 -> retourne toute la flotte (louée, disponible, en maintenance)
        # avec son statut, pour la page catalogue « Nos véhicules ».
        if self.request.query_params.get('all') == '1':
            return _annotate_km_loue(qs)

        start_str = self.request.query_params.get('date_sortie')
        end_str = self.request.query_params.get('date_retour')

        if start_str and end_str:
            try:
                start_date = datetime.fromisoformat(start_str)
                end_date = datetime.fromisoformat(end_str)
            except ValueError:
                start_date = end_date = None

            if start_date and end_date:
                # Disponibilité dans l'intervalle demandé : on exclut les véhicules
                # ayant un contrat (RESERVE/EN_COURS) ou une réservation active
                # (PENDING/CONFIRMED) qui chevauche [date_sortie, date_retour].
                busy_ids = Contract.objects.filter(
                    statut__in=['RESERVE', 'EN_COURS'],
                    date_sortie__lt=end_date,
                    date_retour_prevue__gt=start_date,
                ).values_list('vehicle_id', flat=True).union(
                    Reservation.objects.filter(
                        statut__in=['PENDING', 'CONFIRMED'],
                        date_sortie__lt=end_date,
                        date_retour_prevue__gt=start_date,
                    ).values_list('vehicle_id', flat=True)
                )
                qs = qs.exclude(id__in=busy_ids)
            else:
                # Dates invalides : on conserve le comportement par défaut.
                qs = qs.filter(statut='Available')
        else:
            # Sans dates, on garde le filtre historique « statut Available ».
            qs = qs.filter(statut='Available')

        return _annotate_km_loue(qs)

    @action(detail=True, methods=['get'], url_path='unavailable-dates')
    def unavailable_dates(self, request, pk=None):
        """Plages de dates où le véhicule est indisponible (contrats en cours
        + réservations actives). Utilisé par les datepickers du front web pour
        désactiver les dates non disponibles à la location."""
        vehicle = self.get_object()
        ranges = []

        for c in Contract.objects.filter(
            vehicle=vehicle,
            statut__in=['RESERVE', 'EN_COURS'],
        ).exclude(date_sortie__isnull=True).exclude(date_retour_prevue__isnull=True):
            ranges.append({
                'start': c.date_sortie.isoformat(),
                'end': c.date_retour_prevue.isoformat(),
                'type': 'contract',
                'id': c.id,
            })

        for r in Reservation.objects.filter(
            vehicle=vehicle,
            statut__in=['PENDING', 'CONFIRMED'],
        ).exclude(date_sortie__isnull=True).exclude(date_retour_prevue__isnull=True):
            ranges.append({
                'start': r.date_sortie.isoformat(),
                'end': r.date_retour_prevue.isoformat(),
                'type': 'reservation',
                'id': r.id,
            })

        ranges.sort(key=lambda r: r['start'])
        return Response({'unavailable': ranges})