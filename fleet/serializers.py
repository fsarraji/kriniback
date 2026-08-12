from rest_framework import serializers
from .models import Vehicle, Brand, ModelCar, Evaluation, GpsDevice
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

class GpsDeviceSerializer(serializers.ModelSerializer):
    vehicle_id = serializers.SerializerMethodField()
    vehicle_matricule = serializers.SerializerMethodField()

    class Meta:
        model = GpsDevice
        fields = [
            'id', 'agency', 'vehicle', 'vehicle_id', 'vehicle_matricule',
            'traccar_device_id', 'name', 'unique_id', 'status', 'last_update',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_vehicle_id(self, obj):
        return obj.vehicle_id

    def get_vehicle_matricule(self, obj):
        return obj.vehicle.matricule if obj.vehicle_id else None

class VehicleSerializer(serializers.ModelSerializer):
    marque_name = serializers.CharField(source='marque.name', read_only=True)
    modele_name = serializers.CharField(source='modele.name', read_only=True)
    agency_details = SimpleAgencySerializer(source='agency', read_only=True)
    rating_avg = serializers.SerializerMethodField()
    rating_count = serializers.SerializerMethodField()
    km_loue = serializers.SerializerMethodField()
    traccar_device_id = serializers.IntegerField(required=False, allow_null=True)
    gps_device = GpsDeviceSerializer(read_only=True)

    class Meta:
        model = Vehicle
        fields = '__all__'
        read_only_fields = ['agency', 'gps_device']

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

    def _apply_gps_link(self, vehicle, device_id, provided):
        """Applique l'association demandée via le champ traccar_device_id (si fourni).

        Un ID → lie le dispositif au véhicule (délie tout autre véhicule). Null →
        dissocie. Champ absent → aucune modification.
        """
        if not provided:
            return
        if device_id:
            GpsDevice.attach(vehicle, int(device_id), vehicle.agency)
        else:
            GpsDevice.detach(vehicle)

    def create(self, validated_data):
        provided = 'traccar_device_id' in validated_data
        device_id = validated_data.pop('traccar_device_id', None)
        vehicle = super().create(validated_data)
        self._apply_gps_link(vehicle, device_id, provided)
        return vehicle

    def update(self, instance, validated_data):
        provided = 'traccar_device_id' in validated_data
        device_id = validated_data.pop('traccar_device_id', None)
        vehicle = super().update(instance, validated_data)
        self._apply_gps_link(vehicle, device_id, provided)
        return vehicle

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