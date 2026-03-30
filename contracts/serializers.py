from rest_framework import serializers
from .models import Contract, ContractDamage

class ContractDamageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContractDamage
        fields = ['id', 'type', 'x', 'y', 'description', 'created_at']
        read_only_fields = ['id', 'created_at']

class ContractSerializer(serializers.ModelSerializer):
    damages = ContractDamageSerializer(many=True, required=False)
    client_name = serializers.CharField(source='client.nom', read_only=True)
    client_prenom = serializers.CharField(source='client.prenom', read_only=True)
    client_initials = serializers.SerializerMethodField()
    vehicle_name = serializers.SerializerMethodField()
    vehicle_matricule = serializers.CharField(source='vehicle.matricule', read_only=True)
    formatted_dates = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()

    class Meta:
        model = Contract
        fields = '__all__'
        read_only_fields = ['agency', 'created_by', 'montant_total', 'reste_a_payer']

    def create(self, validated_data):
        damages_data = validated_data.pop('damages', [])
        contract = super().create(validated_data)
        for damage_data in damages_data:
            ContractDamage.objects.create(contract=contract, **damage_data)
        return contract

    def get_client_initials(self, obj):
        return f"{obj.client.nom[0]}{obj.client.prenom[0]}"

    def get_vehicle_name(self, obj):
        return f"{obj.vehicle.marque} {obj.vehicle.modele}"

    def get_formatted_dates(self, obj):
        return {
            'sortie': obj.date_sortie.strftime('%b %d'),
            'retour': obj.date_retour_prevue.strftime('%b %d'),
            'range': f"{obj.date_sortie.strftime('%b %d')} — {obj.date_retour_prevue.strftime('%b %d')}"
        }

    def get_payment_status(self, obj):
        if obj.reste_a_payer <= 0:
            return 'Paid'
        elif obj.montant_paye > 0:
            return 'Partial'
        return 'Unpaid'