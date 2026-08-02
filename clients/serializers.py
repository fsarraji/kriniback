from rest_framework import serializers
from .models import Client
from contracts.models import Contract

class ClientSerializer(serializers.ModelSerializer):
    last_rental = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = '__all__'
        read_only_fields = ['agency']

    def get_last_rental(self, obj):
        last_contract = next((c for c in obj.contracts.all()), None)
        if last_contract:
            return {
                'vehicle': f"{last_contract.vehicle.marque} {last_contract.vehicle.modele}",
                'date': last_contract.date_creation.strftime('%b %d, %Y')
            }
        return None