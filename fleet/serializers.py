from rest_framework import serializers
from .models import Vehicle, Brand, ModelCar, Evaluation
from agency.models import Agency

class SimpleAgencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Agency
        fields = ['id', 'nom_agence', 'adresse', 'ville', 'telephone', 'email']

class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = '__all__'

class ModelCarSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModelCar
        fields = '__all__'

class VehicleSerializer(serializers.ModelSerializer):
    marque_name = serializers.CharField(source='marque.name', read_only=True)
    modele_name = serializers.CharField(source='modele.name', read_only=True)
    agency_details = SimpleAgencySerializer(source='agency', read_only=True)
    rating_avg = serializers.SerializerMethodField()
    rating_count = serializers.SerializerMethodField()
    km_loue = serializers.SerializerMethodField()

    class Meta:
        model = Vehicle
        fields = '__all__'
        read_only_fields = ['agency']

    def get_rating_avg(self, obj):
        evals = obj.evaluations.all()
        if not evals:
            return None
        total = sum(e.rating for e in evals)
        return round(total / len(evals), 1)

    def get_rating_count(self, obj):
        return obj.evaluations.count()

    def get_km_loue(self, obj):
        # Utilise l'annotation (listes) si présente, sinon calcul direct.
        if getattr(obj, 'km_loue', None) is not None:
            return obj.km_loue
        return obj.km_loue_total

class EvaluationSerializer(serializers.ModelSerializer):
    client_name = serializers.SerializerMethodField()
    vehicle_name = serializers.SerializerMethodField()

    class Meta:
        model = Evaluation
        fields = ['id', 'vehicle', 'vehicle_name', 'client', 'client_name', 'rating', 'comment', 'created_at']
        read_only_fields = ['id', 'client', 'client_name', 'vehicle_name', 'created_at']

    def get_client_name(self, obj):
        return f"{obj.client.prenom} {obj.client.nom}" if obj.client else None

    def get_vehicle_name(self, obj):
        return f"{obj.vehicle.marque} {obj.vehicle.modele} - {obj.vehicle.matricule}"

    def validate_rating(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError("La note doit être comprise entre 1 et 5.")
        return value

    def validate(self, data):
        request = self.context.get('request')
        client = getattr(request.user, 'client_profile', None) if request else None
        if not client:
            raise serializers.ValidationError({'detail': 'Compte client requis pour évaluer un véhicule.'})
        vehicle = data.get('vehicle')
        if vehicle and Evaluation.objects.filter(vehicle=vehicle, client=client).exists():
            raise serializers.ValidationError({'detail': 'Vous avez déjà évalué ce véhicule.'})
        return data

    def create(self, validated_data):
        request = self.context['request']
        client = request.user.client_profile
        validated_data['client'] = client
        return super().create(validated_data)