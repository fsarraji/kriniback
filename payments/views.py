from rest_framework import viewsets, permissions
from .models import Payment
from .serializers import PaymentSerializer
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter

class PaymentViewSet(viewsets.ModelViewSet):
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['contract', 'payment_method']
    search_fields = ['reference', 'notes']

    def get_queryset(self):
        return Payment.objects.filter(agency=self.request.user.agency)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, agency=self.request.user.agency)
