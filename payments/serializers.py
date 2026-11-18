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

    def validate_amount(self, amount):
        if amount == 0:
            raise serializers.ValidationError("Le montant ne peut pas être nul.")
        return amount

    def validate(self, data):
        # Création : un encaissement ne peut pas dépasser le reste à payer, et un
        # remboursement (montant négatif) ne peut pas dépasser le total déjà payé.
        if self.instance is None:
            contract = data.get("contract")
            amount = data.get("amount")
            if contract is not None and amount is not None:
                if amount > 0 and amount > contract.reste_a_payer:
                    raise serializers.ValidationError(
                        {
                            "amount": "Le montant dépasse le reste à payer "
                            f"({contract.reste_a_payer})."
                        }
                    )
                if amount < 0 and -amount > contract.montant_paye:
                    raise serializers.ValidationError(
                        {
                            "amount": "Le remboursement dépasse le montant déjà payé "
                            f"({contract.montant_paye})."
                        }
                    )
        return data
