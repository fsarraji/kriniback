from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework import serializers

from .authentication import agency_has_active_subscription

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        user = self.user
        if user.agency_id is not None and not agency_has_active_subscription(user.agency_id):
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Votre agence ne dispose pas d'un abonnement actif. Contactez l'administrateur pour souscrire."
                ]
            })
        return data

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # إضافة معلومات مخصصة داخل التوكن المشفر
        token['username'] = user.username
        
        # Determine role: if superuser, assign SUPERADMIN
        if user.is_superuser:
            token['role'] = 'SUPERADMIN'
        else:
            token['role'] = user.role
            
        token['agency_id'] = user.agency.id if user.agency else None
        token['agency_name'] = user.agency.nom_agence if user.agency else "Super Admin"

        return token