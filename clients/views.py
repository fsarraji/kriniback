from rest_framework import viewsets, permissions, filters
from django_filters.rest_framework import DjangoFilterBackend
from .models import Client
from .serializers import ClientSerializer

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
        if self.request.user.is_superuser:
            return Client.objects.all()
        return Client.objects.filter(agency=self.request.user.agency)

    def perform_create(self, serializer):
        serializer.save(agency=self.request.user.agency)