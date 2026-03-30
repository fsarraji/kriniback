from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
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