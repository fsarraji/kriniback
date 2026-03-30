from rest_framework import serializers
from .models import Vehicle, Brand, ModelCar
from agency.models import Agency

class SimpleAgencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Agency
        fields = ['id', 'name', 'address', 'phone']

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

    class Meta:
        model = Vehicle
        fields = '__all__'
        read_only_fields = ['agency']