from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from .serializers import CustomTokenObtainPairSerializer

User = get_user_model()


class AgencyJWTTests(TestCase):
    def test_superuser_has_superadmin_role_in_token(self):
        # Create a superuser
        superuser = User.objects.create_superuser(
            username="admin_jwt", password="password123", email="admin_jwt@example.com"
        )

        # Get token using the custom serializer
        token = CustomTokenObtainPairSerializer.get_token(superuser)

        # Verify the role in the token
        self.assertEqual(token["role"], "SUPERADMIN")
        self.assertEqual(token["agency_name"], "Super Admin")

    def test_regular_user_has_assigned_role_in_token(self):
        # Create a regular user with a role
        user = User.objects.create_user(
            username="user_jwt", password="password123", role="OWNER"
        )

        # Get token
        token = CustomTokenObtainPairSerializer.get_token(user)

        # Verify the role
        self.assertEqual(token["role"], "OWNER")


class UserAPITests(APITestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="admin_api", password="password123", email="admin_api@example.com"
        )
        self.regular_user = User.objects.create_user(
            username="user_api", password="password123", role="EMPLOYEE"
        )

    def test_superuser_can_list_users(self):
        self.client.force_authenticate(user=self.superuser)
        url = "/api/users/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_regular_user_cannot_list_users(self):
        self.client.force_authenticate(user=self.regular_user)
        url = "/api/users/"
        response = self.client.get(url)
        # Regular users are not IsAdminUser (unless specifically granted, but here they are not)
        self.assertEqual(response.status_code, 403)


class TraccarPasswordEncryptionTests(TestCase):
    def test_password_encrypted_at_rest_and_readable(self):
        from django.db import connection

        from agency.models import Agency

        ag = Agency.objects.create(
            nom_agence="E", adresse="x", telephone="0600", traccar_password="s3cret"
        )
        with connection.cursor() as cur:
            cur.execute(
                "SELECT traccar_password FROM agency_agency WHERE id=%s", [ag.id]
            )
            raw = cur.fetchone()[0]
        self.assertTrue(raw.startswith("enc::"))
        self.assertNotIn("s3cret", raw)
        self.assertEqual(Agency.objects.get(pk=ag.id).traccar_password, "s3cret")


class LogoutBlacklistTests(TestCase):
    def test_refresh_rejected_after_blacklist(self):
        from rest_framework.test import APIClient

        get_user_model().objects.create_user("lo_user", password="pw12345")
        api = APIClient()
        tokens = api.post(
            "/api/token/", {"username": "lo_user", "password": "pw12345"}, format="json"
        ).json()
        self.assertIn("refresh", tokens)
        r = api.post(
            "/api/token/blacklist/", {"refresh": tokens["refresh"]}, format="json"
        )
        self.assertEqual(r.status_code, 200, r.content)
        r2 = api.post(
            "/api/token/refresh/", {"refresh": tokens["refresh"]}, format="json"
        )
        self.assertEqual(r2.status_code, 401)
