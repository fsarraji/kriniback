from rest_framework import serializers
from .models import Client
from contracts.models import Contract
from agency.models import CustomUser

class ClientSerializer(serializers.ModelSerializer):
    last_rental = serializers.SerializerMethodField()
    contrats_count = serializers.SerializerMethodField()

    # Champs uniques au niveau de l'agence (vérifiés après la saisie côté front)
    UNIQUE_FIELDS = {
        'cin_passport': 'CIN/passeport',
        'email': 'email',
        'telephone': 'téléphone',
        'permis_conduite': 'permis de conduire',
    }

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

    def get_contrats_count(self, obj):
        return len(obj.contracts.all())

    def validate(self, data):
        # Normaliser les chaînes vides en None pour éviter les conflits d'unicité
        for field in self.UNIQUE_FIELDS:
            if field in data and data[field] is not None and str(data[field]).strip() == '':
                data[field] = None

        request = self.context.get('request')
        agency = getattr(getattr(request, 'user', None), 'agency', None)

        # Si l'utilisateur n'a pas d'agence (superuser), on contrôle globalement
        if not agency:
            return data

        instance_id = getattr(self.instance, 'pk', None)
        for field, label in self.UNIQUE_FIELDS.items():
            value = data.get(field)
            if value is None or value == '':
                continue
            qs = Client.objects.filter(agency=agency, **{field: value})
            if instance_id:
                qs = qs.exclude(pk=instance_id)
            if qs.exists():
                raise serializers.ValidationError(
                    {field: f"Un client de votre agence utilise déjà cet {label}."}
                )
        return data


class ClientRegisterSerializer(serializers.ModelSerializer):
    """Inscription en ligne d'un client (compte + fiche client)."""
    password = serializers.CharField(write_only=True, min_length=6)
    email = serializers.EmailField(required=False, allow_blank=True)
    date_expiration_cin = serializers.DateField(required=False, allow_null=True)
    nationalite = serializers.CharField(required=False, allow_blank=True)
    sexe = serializers.ChoiceField(choices=[('HOMME', 'Homme'), ('FEMME', 'Femme')], required=False, allow_blank=True)
    date_delivrance_permis = serializers.DateField(required=False, allow_null=True)

    class Meta:
        model = Client
        fields = [
            'nom', 'prenom', 'telephone', 'email',
            'cin_passport', 'date_expiration_cin', 'nationalite', 'sexe',
            'permis_conduite', 'date_delivrance_permis', 'adresse',
            'ville', 'pays',
            'scan_cin', 'scan_permis', 'password',
        ]
        extra_kwargs = {
            'cin_passport': {'required': False, 'allow_blank': True},
            'permis_conduite': {'required': False, 'allow_blank': True},
            'adresse': {'required': False, 'allow_blank': True},
        }

    def validate(self, data):
        cin = (data.get('cin_passport') or '').strip()
        permis = (data.get('permis_conduite') or '').strip()
        if cin and Client.objects.filter(cin_passport=cin).exists():
            raise serializers.ValidationError({'cin_passport': 'Un client avec ce CIN/passeport existe déjà.'})
        if permis and Client.objects.filter(permis_conduite=permis).exists():
            raise serializers.ValidationError({'permis_conduite': 'Un client avec ce permis existe déjà.'})

        email = (data.get('email') or '').strip()
        telephone = (data.get('telephone') or '').strip()
        username = email or telephone
        if not username:
            raise serializers.ValidationError({'email': 'Un email ou un téléphone est requis.'})
        if CustomUser.objects.filter(username=username).exists():
            raise serializers.ValidationError({'email': 'Un compte existe déjà avec ces identifiants.'})
        data['username'] = username
        if not email:
            data['email'] = None
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
        fields = ['id', 'username', 'nom', 'prenom', 'telephone', 'email', 'cin_passport', 'nationalite', 'sexe', 'adresse', 'ville', 'pays', 'scan_cin', 'scan_permis']