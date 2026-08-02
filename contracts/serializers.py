from rest_framework import serializers
from .models import Contract, ContractDamage, PdfJob, BookingRequest
from django.db.models import Q


class BookingRequestSerializer(serializers.ModelSerializer):
    vehicle_name = serializers.SerializerMethodField()

    class Meta:
        model = BookingRequest
        fields = ['id', 'agency', 'vehicle', 'vehicle_name', 'nom', 'prenom', 'telephone', 'email', 'message', 'date_sortie', 'date_retour_prevue', 'statut', 'created_at']
        read_only_fields = ['id', 'agency', 'statut', 'created_at']

    def get_vehicle_name(self, obj):
        if not obj.vehicle:
            return None
        return f"{obj.vehicle.marque} {obj.vehicle.modele} - {obj.vehicle.matricule}"

    def validate(self, data):
        vehicle = data.get('vehicle')
        if not vehicle:
            raise serializers.ValidationError({'vehicle': 'Le véhicule est requis.'})

        date_sortie = data.get('date_sortie')
        date_retour_prevue = data.get('date_retour_prevue')
        if date_sortie and date_retour_prevue and date_retour_prevue <= date_sortie:
            raise serializers.ValidationError("La date de retour doit être postérieure à la date de sortie.")

        return data

    def create(self, validated_data):
        vehicle = validated_data['vehicle']
        validated_data['agency'] = vehicle.agency
        return super().create(validated_data)

class ContractDamageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContractDamage
        fields = ['id', 'type', 'x', 'y', 'description', 'created_at']
        read_only_fields = ['id', 'created_at']

class PdfJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = PdfJob
        fields = ['id', 'contract', 'job_type', 'with_cachet', 'status', 'error_message', 'created_at', 'updated_at']
        read_only_fields = ['id', 'status', 'error_message', 'created_at', 'updated_at']

class ContractSerializer(serializers.ModelSerializer):
    damages = ContractDamageSerializer(many=True, required=False)
    client_name = serializers.CharField(source='client.nom', read_only=True)
    client_prenom = serializers.CharField(source='client.prenom', read_only=True)
    client_initials = serializers.SerializerMethodField()
    vehicle_name = serializers.SerializerMethodField()
    vehicle_matricule = serializers.CharField(source='vehicle.matricule', read_only=True)
    vehicle_tarif_km_extra = serializers.DecimalField(source='vehicle.tarif_km_extra', max_digits=6, decimal_places=2, read_only=True, allow_null=True)
    formatted_dates = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()

    class Meta:
        model = Contract
        fields = '__all__'
        read_only_fields = ['agency', 'created_by', 'montant_total', 'reste_a_payer']

    def create(self, validated_data):
        damages_data = validated_data.pop('damages', [])
        
        # 1. Extraire les informations de paiement initiales
        montant_paye = validated_data.pop('montant_paye', 0)
        methode_paiement = validated_data.get('methode_paiement', 'Espèce')
        
        # 2. Forcer le montant initial à 0 pour éviter le double comptage
        # car le modèle Payment mettra à jour ce montant via son save()
        validated_data['montant_paye'] = 0
        
        contract = super().create(validated_data)
        
        # 3. Sauvegarder les dommages
        for damage_data in damages_data:
            ContractDamage.objects.create(contract=contract, **damage_data)
            
        # 4. Créer la trace du paiement si une avance a été versée
        if montant_paye > 0:
            from payments.models import Payment
            Payment.objects.create(
                agency=contract.agency,
                contract=contract,
                user=contract.created_by,
                amount=montant_paye,
                payment_method=methode_paiement,
                notes="Avance initiale (Création)"
            )
            
        return contract

    def update(self, instance, validated_data):
        damages_data = validated_data.pop('damages', None)
        instance = super().update(instance, validated_data)
        
        if damages_data is not None:
            for damage_data in damages_data:
                # Assuming damages sent during update are additive or replace existing ones?
                # For Activation, we just create the DEPART damages.
                ContractDamage.objects.create(contract=instance, **damage_data)
                
        return instance

    def validate(self, data):
        vehicle = data.get('vehicle')
        # If updating, use existing data if not provided
        if self.instance and not vehicle:
            vehicle = self.instance.vehicle
            
        date_sortie = data.get('date_sortie')
        if self.instance and not date_sortie:
            date_sortie = self.instance.date_sortie
            
        date_retour_prevue = data.get('date_retour_prevue')
        if self.instance and not date_retour_prevue:
            date_retour_prevue = self.instance.date_retour_prevue

        if vehicle and date_sortie and date_retour_prevue:
            # Check for overlapping contracts (EN_COURS or RESERVE)
            overlapping = Contract.objects.filter(
                vehicle=vehicle,
                statut__in=['EN_COURS', 'RESERVE'],
                date_sortie__lt=date_retour_prevue,
                date_retour_prevue__gt=date_sortie
            )
            if self.instance:
                overlapping = overlapping.exclude(pk=self.instance.pk)
                
            if overlapping.exists():
                raise serializers.ValidationError("Ce véhicule est déjà loué ou réservé pour la période sélectionnée.")

        return data

    def get_client_initials(self, obj):
        n = (obj.client.nom or '')
        p = (obj.client.prenom or '')
        return f"{n[0] if n else ''}{p[0] if p else ''}"

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