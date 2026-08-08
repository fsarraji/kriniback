from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import mail
from rest_framework.test import APIClient

from PIL import Image
from io import BytesIO

from agency.models import Agency, CustomUser
from clients.models import Client


def make_png(name='cin.jpg'):
    buf = BytesIO()
    Image.new('RGB', (100, 100), color=(200, 50, 50)).save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ClientDocumentsUploadTest(TestCase):
    def setUp(self):
        self.agency = Agency.objects.create(
            nom_agence='TEST AG', adresse='x', telephone='0612345678',
            email='agence@test.com',
        )
        self.user = CustomUser.objects.create_user(
            username='testclient', password='pass12345', role='CLIENT', agency=self.agency,
        )
        self.client = Client.objects.create(
            agency=self.agency, user=self.user, nom='Alaoui', prenom='Ahmed',
            telephone='0612345678', cin_passport='TMP-TEST', permis_conduite='TMP-TEST',
        )

    def test_upload_documents_sends_email_to_agency(self):
        c = APIClient()
        c.force_authenticate(user=self.user)

        png = make_png('cin.jpg')
        resp = c.patch('/api/clients/me/', {'scan_cin': png}, format='multipart')

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data.get('scan_cin'))
        self.client.refresh_from_db()
        self.assertTrue(self.client.scan_cin)

        # le thread envoie l'email -> on attend un peu
        import time
        time.sleep(1.5)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.agency.email])
        self.assertEqual(len(mail.outbox[0].attachments), 1)
        self.assertTrue(mail.outbox[0].attachments[0][0].startswith('cin'))

    def test_profile_exposes_scan_fields(self):
        c = APIClient()
        c.force_authenticate(user=self.user)
        resp = c.get('/api/clients/me/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('scan_cin', resp.data)
        self.assertIn('scan_permis', resp.data)
