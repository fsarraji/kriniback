from rest_framework import serializers
from .models import Client
from contracts.models import Contract
from agency.models import CustomUser

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


class ClientRegisterSerializer(serializers.ModelSerializer):
    """Inscription en ligne d'un client (compte + fiche client)."""
    password = serializers.CharField(write_only=True, min_length=6)
    email = serializers.EmailField(required=False, allow_blank=True)
    date_expiration_cin = serializers.DateField(required=False, allow_null=True)
    nationalite = serializers.CharField(required=False, allow_blank=True)
    date_delivrance_permis = serializers.DateField(required=False, allow_null=True)

    class Meta:
        model = Client
        fields = [
            'nom', 'prenom', 'telephone', 'email',
            'cin_passport', 'date_expiration_cin', 'nationalite',
            'permis_conduite', 'date_delivrance_permis', 'adresse',
            'scan_cin', 'scan_permis', 'password',
        ]
        extra_kwargs = {
            'cin_passport': {'required': True},
            'permis_conduite': {'required': True},
            'adresse': {'required': True},
        }

    def validate(self, data):
        if Client.objects.filter(cin_passport=data.get('cin_passport')).exists():
            raise serializers.ValidationError({'cin_passport': 'Un client avec ce CIN/passeport existe déjà.'})
        if Client.objects.filter(permis_conduite=data.get('permis_conduite')).exists():
            raise serializers.ValidationError({'permis_conduite': 'Un client avec ce permis existe déjà.'})

        email = data.get('email')
        telephone = data.get('telephone')
        username = (email or telephone or '').strip()
        if not username:
            raise serializers.ValidationError({'email': 'Un email ou un téléphone est requis.'})
        if CustomUser.objects.filter(username=username).exists():
            raise serializers.ValidationError({'email': 'Un compte existe déjà avec ces identifiants.'})
        data['username'] = username
        return data

    def create(self, validated_data):
        password = validated_data.pop('password')
        username = validated_data.pop('username')
        user = CustomUser(username=username, role='CLIENT')
        user.set_password(password)
        user.save()
        client = Client.objects.create(user=user, **validated_data)
        return client


class ClientAccountSerializer(serializers.ModelSerializer):
    """Profil du client connecté (renvoie aussi les identifiants du compte)."""
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = Client
        fields = ['id', 'username', 'nom', 'prenom', 'telephone', 'email', 'cin_passport', 'adresse']