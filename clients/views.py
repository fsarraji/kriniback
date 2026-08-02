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
            return base
        return base.filter(agency=self.request.user.agency)

    def perform_create(self, serializer):
        serializer.save(agency=self.request.user.agency)


class ClientRegisterView(APIView):
    """Inscription publique d'un client (compte + fiche avec documents)."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = ClientRegisterSerializer(data=request.data)
        if serializer.is_valid():
            client = serializer.save()
            return Response({
                'id': client.id,
                'nom': client.nom,
                'prenom': client.prenom,
                'detail': 'Compte client créé avec succès.',
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ClientAccountView(APIView):
    """Profil du client connecté."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        client = getattr(request.user, 'client_profile', None)
        if not client:
            return Response({'detail': 'Aucun profil client lié à ce compte.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(ClientAccountSerializer(client).data)