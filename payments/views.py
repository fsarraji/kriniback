from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import permissions, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.filters import SearchFilter

from car_rental_backend.permissions import IsAgencyStaff

from .models import Payment
from .serializers import PaymentSerializer


class PaymentViewSet(viewsets.ModelViewSet):
    serializer_class = PaymentSerializer
    permission_classes = [IsAgencyStaff]
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["contract", "payment_method"]
    search_fields = ["reference", "notes"]

    def get_queryset(self):
        return Payment.objects.filter(agency=self.request.user.agency)

    def perform_create(self, serializer):
        if not self.request.user.agency:
            raise ValidationError({"detail": "Compte non lié à une agence."})
        serializer.save(user=self.request.user, agency=self.request.user.agency)
