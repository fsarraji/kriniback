from django.test import TestCase
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from .serializers import CustomTokenObtainPairSerializer

User = get_user_model()

class AgencyJWTTests(TestCase):
    def test_superuser_has_superadmin_role_in_token(self):
        # Create a superuser
        superuser = User.objects.create_superuser(
            username='admin_jwt',
            password='password123',
            email='admin_jwt@example.com'
        )
        
        # Get token using the custom serializer
        token = CustomTokenObtainPairSerializer.get_token(superuser)
        
        # Verify the role in the token
        self.assertEqual(token['role'], 'SUPERADMIN')
        self.assertEqual(token['agency_name'], 'Super Admin')

    def test_regular_user_has_assigned_role_in_token(self):
        # Create a regular user with a role
        user = User.objects.create_user(
            username='user_jwt',
            password='password123',
            role='OWNER'
        )
        
        # Get token
        token = CustomTokenObtainPairSerializer.get_token(user)
        
        # Verify the role
        self.assertEqual(token['role'], 'OWNER')

class UserAPITests(APITestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username='admin_api',
            password='password123',
            email='admin_api@example.com'
        )
        self.regular_user = User.objects.create_user(
            username='user_api',
            password='password123',
            role='EMPLOYEE'
        )

    def test_superuser_can_list_users(self):
        self.client.force_authenticate(user=self.superuser)
        url = '/api/users/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_regular_user_cannot_list_users(self):
        self.client.force_authenticate(user=self.regular_user)
        url = '/api/users/'
        response = self.client.get(url)
        # Regular users are not IsAdminUser (unless specifically granted, but here they are not)
        self.assertEqual(response.status_code, 403)
