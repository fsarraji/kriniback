from rest_framework import viewsets, permissions, filters, status
from rest_framework.views import APIView
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Prefetch
from .models import Client
from contracts.models import Contract
from .serializers import ClientSerializer, ClientRegisterSerializer, ClientAccountSerializer

class ClientViewSet(viewsets.ModelViewSet):
    serializer_class = ClientSerializer
    permission_classes =[permissions.IsAuthenticated]
    
    # إضافة محركات البحث والفلترة
    filter_backends =[DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    
    # 1. البحث النصي السريع (Search)
    search_fields = ['cin_passport', 'nom', 'prenom', 'telephone']
    
    # 2. الفلترة الدقيقة (Filter) - مثلاً جلب الزبناء في القائمة السوداء فقط
    filterset_fields = ['liste_noire']
    
    # 3. الترتيب (Ordering)
    ordering_fields = ['nom', 'id']

    def get_queryset(self):
        base = Client.objects.prefetch_related(
            Prefetch(
                'contracts',
                queryset=Contract.objects.select_related('vehicle__marque', 'vehicle__modele').order_by('-date_creation')
            )
        )
        if self.request.user.is_superuser:
            qs = base
        else:
            qs = base.filter(agency=self.request.user.agency)
        # Suppression douce : masqués pour tous sauf le super admin qui consulte les clients supprimés.
        if self.request.query_params.get('include_deleted') != '1' or not self.request.user.is_superuser:
            qs = qs.filter(is_deleted=False)
        return qs

    def perform_create(self, serializer):
        serializer.save(agency=self.request.user.agency)

    def destroy(self, request, *args, **kwargs):
        """Suppression douce : le client n'est pas supprimé de la base,
        il est masqué de l'annuaire et pourra être restauré par le super admin."""
        obj = self.get_object()
        obj.is_deleted = True
        obj.save(update_fields=['is_deleted'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class ClientCheckUniqueView(APIView):
    """Vérifie en temps réel (après la saisie) si une valeur est déjà utilisée
    par un autre client de la même agence."""
    permission_classes = [permissions.IsAuthenticated]

    ALLOWED_FIELDS = {'cin_passport', 'email', 'telephone', 'permis_conduite'}

    def get(self, request):
        field = request.query_params.get('field', '')
        value = (request.query_params.get('value') or '').strip()
        exclude_id = request.query_params.get('exclude_id')

        if field not in self.ALLOWED_FIELDS or not value:
            return Response({'available': True, 'field': field, 'value': value})

        qs = Client.objects.all()
        if not request.user.is_superuser:
            qs = qs.filter(agency=request.user.agency)
        if exclude_id:
            qs = qs.exclude(pk=exclude_id)

        exists = qs.filter(**{field: value}).exists()
        return Response({
            'available': not exists,
            'field': field,
            'value': value,
        })


class ClientRegisterView(APIView):
    """Inscription publique d'un client (compte + fiche avec documents)."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = ClientRegisterSerializer(data=request.data)
        if serializer.is_valid():
            client = serializer.save()
            # Retourne les tokens JWT pour connecter immédiatement le client
            from agency.serializers import CustomTokenObtainPairSerializer
            refresh = CustomTokenObtainPairSerializer.get_token(client.user)
            return Response({
                'id': client.id,
                'nom': client.nom,
                'prenom': client.prenom,
                'detail': 'Compte client créé avec succès.',
                'access': str(refresh.access_token),
                'refresh': str(refresh),
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ClientAccountView(APIView):
    """Profil du client connecté."""
    permission_classes = [permissions.IsAuthenticated]

    def get_client(self, request):
        client = getattr(request.user, 'client_profile', None)
        if not client:
            return None
        return client

    def get(self, request):
        client = self.get_client(request)
        if not client:
            return Response({'detail': 'Aucun profil client lié à ce compte.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(ClientAccountSerializer(client).data)

    def patch(self, request):
        client = self.get_client(request)
        if not client:
            return Response({'detail': 'Aucun profil client lié à ce compte.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = ClientAccountSerializer(client, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            # Si le client a fourni des scans, on les envoie à l'email de son agence
            if ('scan_cin' in request.FILES or 'scan_permis' in request.FILES) and client.agency and client.agency.email:
                from clients.services import send_client_documents_to_agency
                reservation = client.reservations.filter(statut='CONFIRMED').order_by('-created_at').first()
                send_client_documents_to_agency(client, client.agency, reservation)
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def put(self, request):
        return self.patch(request)