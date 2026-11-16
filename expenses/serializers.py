from rest_framework import serializers

from car_rental_backend.permissions import check_same_agency

from .models import Expense


class ExpenseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Expense
        fields = "__all__"
        read_only_fields = ["agency", "user", "created_at"]

    def validate_vehicle(self, vehicle):
        check_same_agency(self.context.get("request"), vehicle, "vehicle")
        return vehicle
