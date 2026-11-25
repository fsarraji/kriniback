from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import permissions, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.filters import SearchFilter

from .models import Expense
from .serializers import ExpenseSerializer


class ExpenseViewSet(viewsets.ModelViewSet):
    serializer_class = ExpenseSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["vehicle", "category"]
    search_fields = ["title", "notes"]

    def get_queryset(self):
        # Each user sees only their own agency's expenses
        return Expense.objects.filter(agency=self.request.user.agency)

    def perform_create(self, serializer):
        if not self.request.user.agency:
            raise ValidationError({"detail": "Compte non lié à une agence."})
        serializer.save(user=self.request.user, agency=self.request.user.agency)
