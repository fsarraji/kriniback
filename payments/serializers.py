from rest_framework import serializers

from car_rental_backend.permissions import check_same_agency

from .models import Payment


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = "__all__"
        read_only_fields = ["agency", "user", "created_at"]

    def validate_contract(self, contract):
        check_same_agency(self.context.get("request"), contract, "contract")
        return contract
