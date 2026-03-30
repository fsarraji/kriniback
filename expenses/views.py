from rest_framework import viewsets, permissions
from .models import Expense
from .serializers import ExpenseSerializer
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter

class ExpenseViewSet(viewsets.ModelViewSet):
    serializer_class = ExpenseSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['vehicle', 'category']
    search_fields = ['title', 'notes']

    def get_queryset(self):
        # Each user sees only their own agency's expenses
        return Expense.objects.filter(agency=self.request.user.agency)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, agency=self.request.user.agency)
