from rest_framework_simplejwt.views import TokenObtainPairView
from .serializers import CustomTokenObtainPairSerializer

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from django.utils import timezone
from datetime import timedelta
from django.db.models import Sum

from fleet.models import Vehicle, Brand
from contracts.models import Contract
from clients.models import Client
from .models import Agency, CustomUser
from rest_framework import serializers, viewsets, filters
from rest_framework.permissions import IsAdminUser
from django_filters.rest_framework import DjangoFilterBackend

class AgencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Agency
        fields = '__all__'

class UserSerializer(serializers.ModelSerializer):
    agency_name = serializers.ReadOnlyField(source='agency.nom_agence')
    
    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'role', 'agency', 'agency_name', 'is_active', 'password']
        extra_kwargs = {'password': {'write_only': True, 'required': False}}

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        user = super().create(validated_data)
        if password:
            user.set_password(password)
            user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        user = super().update(instance, validated_data)
        if password:
            user.set_password(password)
            user.save()
        return user

class AgencyViewSet(viewsets.ModelViewSet):
    queryset = Agency.objects.all()
    serializer_class = AgencySerializer
    permission_classes = [IsAdminUser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['nom_agence', 'adresse', 'ville', 'telephone', 'email', 'rc', 'ice']
    ordering_fields = ['nom_agence', 'id']

class PublicAgencySerializer(serializers.ModelSerializer):
    vehicles_count = serializers.IntegerField(source='vehicles.count', read_only=True)

    class Meta:
        model = Agency
        fields = ['id', 'nom_agence', 'adresse', 'ville', 'telephone', 'email', 'logo',
                  'caution_active', 'caution_montant', 'km_extra_active', 'km_par_jour',
                  'km_tarif_extra_defaut', 'vehicles_count']

class PublicAgencyViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Vue publique (sans authentification) des agences pour le site de réservation.
    """
    queryset = Agency.objects.filter(is_active=True, vehicles__statut='Available').distinct()
    serializer_class = PublicAgencySerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None

class UserViewSet(viewsets.ModelViewSet):
    queryset = CustomUser.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAdminUser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['role', 'agency', 'is_active']
    search_fields = ['username', 'email', 'first_name', 'last_name']
    ordering_fields = ['username', 'id']


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class DashboardStatsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        agency = request.user.agency
        today = timezone.now().date()
        thirty_days_later = today + timedelta(days=30)

        # Base querysets
        vehicles_qs = Vehicle.objects.all()
        contracts_qs = Contract.objects.all()
        clients_qs = Client.objects.all()

        # Filter by agency if not a superadmin
        if agency:
            vehicles_qs = vehicles_qs.filter(agency=agency)
            contracts_qs = contracts_qs.filter(agency=agency)
            clients_qs = clients_qs.filter(agency=agency)
        elif not request.user.is_superuser:
            # If no agency and not superuser, return nothing (safety)
            vehicles_qs = vehicles_qs.none()
            contracts_qs = contracts_qs.none()
            clients_qs = clients_qs.none()

        # 1. إحصائيات عامة
        total_vehicles = vehicles_qs.count()
        available_vehicles = vehicles_qs.filter(statut='Available').count()
        rented_vehicles = vehicles_qs.filter(statut='Rented').count()
        active_contracts = contracts_qs.filter(statut='EN_COURS').count()
        total_clients = clients_qs.count()

        # 2. المداخيل الإجمالية (هذا الشهر كمثال)
        current_month = timezone.now().month
        revenue = contracts_qs.filter(
            date_creation__month=current_month
        ).aggregate(Sum('montant_total'))['montant_total__sum'] or 0

        # 3. التنبيهات (Alerts) - السيارات التي سينتهي تأمينها أو فحصها التقني قريباً (أقل من 30 يوم)
        insurance_alerts = vehicles_qs.filter(
            date_assurance__lte=thirty_days_later
        ).values('id', 'matricule', 'marque', 'date_assurance')

        visite_alerts = vehicles_qs.filter(
            date_visite_technique__lte=thirty_days_later
        ).values('id', 'matricule', 'marque', 'date_visite_technique')

        # 4. العقود الأخيرة (آخر 5)
        recent_contracts = contracts_qs.order_by('-date_creation')[:5].values(
            'id', 'client__nom', 'client__prenom', 'vehicle__marque', 'vehicle__modele', 'vehicle__matricule', 'jours', 'montant_total', 'statut'
        )

        # إرجاع البيانات على شكل JSON منظم
        return Response({
            'stats': {
                'total_vehicles': total_vehicles,
                'available_vehicles': available_vehicles,
                'rented_vehicles': rented_vehicles,
                'active_contracts': active_contracts,
                'total_clients': total_clients,
                'revenue_this_month': revenue,
                'agency_name': agency.nom_agence if agency else "Super Admin"
            },
            'alerts': {
                'insurance_expiring': list(insurance_alerts),
                'visite_expiring': list(visite_alerts)
            },
            'recent_contracts': list(recent_contracts)
        })

class AgencySettingsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not request.user.agency:
            return Response({"error": "No agency attached"}, status=400)
        agency = request.user.agency
        return Response({
            "nom_agence": agency.nom_agence,
            "adresse": agency.adresse,
            "ville": agency.ville,
            "telephone": agency.telephone,
            "email": agency.email,
            "caution_active": agency.caution_active,
            "caution_montant": str(agency.caution_montant),
            "km_extra_active": agency.km_extra_active,
            "km_par_jour": agency.km_par_jour,
            "km_tarif_extra_defaut": str(agency.km_tarif_extra_defaut),
            "cachet_signature": request.build_absolute_uri(agency.cachet_signature.url) if agency.cachet_signature else None,
            "brands": list(agency.brands.values_list('id', flat=True)),
        })

    def put(self, request):
        if not request.user.agency:
            return Response({"error": "No agency attached"}, status=400)
        
        # Only owner or superadmin can modify
        if request.user.role != 'OWNER' and not request.user.is_superuser:
            return Response({"error": "Permission denied. Only Owner can modify settings."}, status=403)
            
        agency = request.user.agency
        
        if 'email' in request.data:
            agency.email = (str(request.data['email']).strip() or None)

        if 'telephone' in request.data:
            agency.telephone = str(request.data['telephone']).strip()

        if 'adresse' in request.data:
            agency.adresse = str(request.data['adresse']).strip()

        if 'ville' in request.data:
            agency.ville = str(request.data['ville']).strip()

        if 'caution_active' in request.data:
            agency.caution_active = str(request.data['caution_active']).lower() in ['true', '1', 't', 'y', 'yes']
            
        if 'caution_montant' in request.data:
            try:
                agency.caution_montant = float(request.data['caution_montant'])
            except ValueError:
                pass

        if 'km_extra_active' in request.data:
            agency.km_extra_active = str(request.data['km_extra_active']).lower() in ['true', '1', 't', 'y', 'yes']

        if 'km_par_jour' in request.data:
            try:
                agency.km_par_jour = int(request.data['km_par_jour'])
            except (ValueError, TypeError):
                pass

        if 'km_tarif_extra_defaut' in request.data:
            try:
                agency.km_tarif_extra_defaut = float(request.data['km_tarif_extra_defaut'])
            except (ValueError, TypeError):
                pass

        if 'cachet_signature' in request.FILES:
            agency.cachet_signature = request.FILES['cachet_signature']

        if 'brands' in request.data:
            # Supporte à la fois list JSON (axios) et QueryDict multi-valeurs (FormData)
            raw = request.data.getlist('brands') if hasattr(request.data, 'getlist') else request.data.get('brands')
            if isinstance(raw, str):
                raw = [raw]
            brand_ids = []
            for b in raw:
                try:
                    brand_ids.append(int(b))
                except (ValueError, TypeError):
                    continue
            agency.brands.set(Brand.objects.filter(id__in=brand_ids))

        agency.save()
        agency.refresh_from_db(fields=['brands'])
        
        return Response({
            "message": "Settings updated",
            "nom_agence": agency.nom_agence,
            "adresse": agency.adresse,
            "ville": agency.ville,
            "telephone": agency.telephone,
            "email": agency.email,
            "caution_active": agency.caution_active,
            "caution_montant": str(agency.caution_montant),
            "km_extra_active": agency.km_extra_active,
            "km_par_jour": agency.km_par_jour,
            "km_tarif_extra_defaut": str(agency.km_tarif_extra_defaut),
            "cachet_signature": request.build_absolute_uri(agency.cachet_signature.url) if agency.cachet_signature else None,
            "brands": list(agency.brands.values_list('id', flat=True)),
        })


def user_payload(user):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "role": 'SUPERADMIN' if user.is_superuser else user.role,
        "agency_id": user.agency.id if user.agency else None,
        "agency_name": user.agency.nom_agence if user.agency else None,
    }


class AccountMeView(APIView):
    """Compte de l'utilisateur connecté (staff / owner / superadmin)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(user_payload(request.user))

    def put(self, request):
        user = request.user
        data = request.data

        if 'username' in data:
            username = str(data['username']).strip()
            if not username:
                return Response({"username": "Le nom d'utilisateur est requis."}, status=400)
            if CustomUser.objects.exclude(pk=user.pk).filter(username=username).exists():
                return Response({"username": "Ce nom d'utilisateur est déjà pris."}, status=400)
            user.username = username

        if 'first_name' in data:
            user.first_name = str(data['first_name'])[:150]
        if 'last_name' in data:
            user.last_name = str(data['last_name'])[:150]
        if 'email' in data:
            user.email = str(data['email']).strip() or None

        new_password = data.get('new_password')
        if new_password:
            current_password = data.get('current_password', '')
            if not current_password or not user.check_password(str(current_password)):
                return Response({"current_password": "Le mot de passe actuel est incorrect."}, status=400)
            if len(str(new_password)) < 6:
                return Response({"new_password": "Le mot de passe doit contenir au moins 6 caractères."}, status=400)
            user.set_password(str(new_password))

        user.save()
        return Response(user_payload(user))