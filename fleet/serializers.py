from rest_framework import serializers
from .models import Vehicle, Brand, ModelCar, Evaluation, GpsDevice, VehiclePriceInterval
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
        return obj.vehicle.matricule_actuel if obj.vehicle_id else None


class VehiclePriceIntervalSerializer(serializers.ModelSerializer):
    class Meta:
        model = VehiclePriceInterval
        fields = ['id', 'type', 'prix', 'date_debut', 'date_fin',
                  'mois_debut', 'jour_debut', 'mois_fin', 'jour_fin']

    def validate(self, data):
        instance = self.instance
        prix = data.get('prix', getattr(instance, 'prix', None))
        if prix is not None and prix <= 0:
            raise serializers.ValidationError({'prix': 'Le prix doit être supérieur à zéro.'})

        inter_type = data.get('type', getattr(instance, 'type', 'RECURRENT'))
        if inter_type == 'ABSOLUTE':
            debut = data.get('date_debut', getattr(instance, 'date_debut', None))
            fin = data.get('date_fin', getattr(instance, 'date_fin', None))
            if not debut or not fin:
                raise serializers.ValidationError({'date_fin': 'Renseignez la date de début et la date de fin.'})
            if fin < debut:
                raise serializers.ValidationError({'date_fin': 'La date de fin doit être postérieure ou égale à la date de début.'})
        else:
            missing = [k for k in ('mois_debut', 'jour_debut', 'mois_fin', 'jour_fin')
                       if data.get(k, getattr(instance, k, None)) is None]
            if missing:
                raise serializers.ValidationError({'mois_debut': 'Renseignez le mois et le jour de début et de fin.'})
            for key in ('mois_debut', 'mois_fin'):
                val = data.get(key, getattr(instance, key, None))
                if val is not None and not (1 <= val <= 12):
                    raise serializers.ValidationError({key: 'Le mois doit être compris entre 1 et 12.'})
            for key in ('jour_debut', 'jour_fin'):
                val = data.get(key, getattr(instance, key, None))
                if val is not None and not (1 <= val <= 31):
                    raise serializers.ValidationError({key: 'Le jour doit être compris entre 1 et 31.'})
        return data


class VehiclePriceIntervalsField(serializers.Field):
    """Champ imbriqué acceptant une liste de dicts (JSON) ou une chaîne JSON
    (formulaires multipart envoyés par les fronts web / mobile)."""

    def to_representation(self, value):
        return VehiclePriceIntervalSerializer(value.all(), many=True).data

    def to_internal_value(self, data):
        if data is None:
            return []
        if isinstance(data, str):
            import json
            try:
                data = json.loads(data)
            except (ValueError, TypeError):
                raise serializers.ValidationError('price_intervals doit être une liste au format JSON.')
        if not isinstance(data, (list, tuple)):
            raise serializers.ValidationError('price_intervals doit être une liste.')
        items = []
        for item in data:
            serializer = VehiclePriceIntervalSerializer(data=dict(item))
            serializer.is_valid(raise_exception=True)
            items.append(serializer.validated_data)
        return items

class VehicleSerializer(serializers.ModelSerializer):
    marque_name = serializers.CharField(source='marque.name', read_only=True)
    modele_name = serializers.CharField(source='modele.name', read_only=True)
    agency_details = SimpleAgencySerializer(source='agency', read_only=True)
    rating_avg = serializers.SerializerMethodField()
    rating_count = serializers.SerializerMethodField()
    km_loue = serializers.SerializerMethodField()
    traccar_device_id = serializers.IntegerField(required=False, allow_null=True)
    gps_device = GpsDeviceSerializer(read_only=True)
    matricule_actuel = serializers.CharField(read_only=True)
    price_intervals = VehiclePriceIntervalsField(required=False)

    class Meta:
        model = Vehicle
        fields = '__all__'
        read_only_fields = ['agency', 'gps_device']

    def validate_matricule_definitif(self, value):
        value = (value or '').strip()
        return value or None

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

    def _apply_intervals(self, vehicle, intervals, provided):
        """Remplace les périodes tarifaires du véhicule (si fournies)."""
        if not provided:
            return
        self._check_interval_overlaps(intervals)
        vehicle.price_intervals.all().delete()
        for data in intervals:
            VehiclePriceInterval.objects.create(vehicle=vehicle, **data)

    @staticmethod
    def _check_interval_overlaps(intervals):
        """Refuse deux périodes du même type qui se chevauchent."""
        def md(item, prefix):
            return (item[f'{prefix}_mois'], item[f'{prefix}_jour'])

        def in_range(m, start, end):
            if start <= end:
                return start <= m <= end
            return m >= start or m <= end  # chevauchement d'année

        def recurrent_overlap(a, b):
            sa, ea = md(a, 'debut'), md(a, 'fin')
            sb, eb = md(b, 'debut'), md(b, 'fin')
            return in_range(sb, sa, ea) or in_range(eb, sa, ea) or in_range(sa, sb, eb) or in_range(ea, sb, eb)

        def absolute_overlap(a, b):
            return a['date_debut'] <= b['date_fin'] and b['date_debut'] <= a['date_fin']

        for i, a in enumerate(intervals):
            for b in intervals[i + 1:]:
                if a['type'] != b['type']:
                    continue
                overlap = absolute_overlap(a, b) if a['type'] == 'ABSOLUTE' else recurrent_overlap(a, b)
                if overlap:
                    raise serializers.ValidationError({
                        'price_intervals': 'Deux périodes tarifaires du même type ne peuvent pas se chevaucher.'
                    })

    def create(self, validated_data):
        intervals = validated_data.pop('price_intervals', None)
        provided = 'traccar_device_id' in validated_data
        device_id = validated_data.pop('traccar_device_id', None)
        vehicle = super().create(validated_data)
        self._apply_intervals(vehicle, intervals, intervals is not None)
        self._apply_gps_link(vehicle, device_id, provided)
        return vehicle

    def update(self, instance, validated_data):
        intervals = validated_data.pop('price_intervals', None)
        provided = 'traccar_device_id' in validated_data
        device_id = validated_data.pop('traccar_device_id', None)
        vehicle = super().update(instance, validated_data)
        self._apply_intervals(vehicle, intervals, intervals is not None)
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