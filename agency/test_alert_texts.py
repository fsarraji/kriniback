import re
from datetime import timedelta

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from agency.alert_texts import TEMPLATES, lang_from_request, render_alert
from agency.models import Notification
from agency.notifications import generate_alerts_for_agency
from fleet.test_maintenance import make_agency, make_vehicle


class TemplateTests(SimpleTestCase):
    def test_every_code_has_french_and_arabic_with_same_variables(self):
        def variables(text):
            return sorted(re.findall(r"\{(\w+)\}", text))

        for code, langs in TEMPLATES.items():
            self.assertEqual(set(langs), {"fr", "ar"}, code)
            for part in (0, 1):
                self.assertEqual(
                    variables(langs["fr"][part]), variables(langs["ar"][part]), code
                )

    def test_render_and_fallbacks(self):
        params = {"plate": "79-1146", "days": 3}
        self.assertEqual(
            render_alert("INSURANCE_EXPIRED", params, "fr")[0],
            "Assurance expirée - 79-1146",
        )
        self.assertIn("79-1146", render_alert("INSURANCE_EXPIRED", params, "ar")[0])
        # langue inconnue : français ; code inconnu : None
        self.assertEqual(
            render_alert("INSURANCE_EXPIRED", params, "de"),
            render_alert("INSURANCE_EXPIRED", params, "fr"),
        )
        self.assertIsNone(render_alert("NOPE", params, "fr"))
        # variable manquante : laissée telle quelle, sans exception
        self.assertIn("{days}", render_alert("INSURANCE_EXPIRED", {"plate": "X"}, "fr")[1])

    def test_language_header(self):
        class R:
            META = {"HTTP_ACCEPT_LANGUAGE": "ar-MA,ar;q=0.9"}

        class F:
            META = {"HTTP_ACCEPT_LANGUAGE": "fr-FR"}

        self.assertEqual(lang_from_request(R()), "ar")
        self.assertEqual(lang_from_request(F()), "fr")
        self.assertEqual(lang_from_request(None), "fr")


class NotificationApiLanguageTests(TestCase):
    def setUp(self):
        self.ag, self.owner, brand, model = make_agency("AL")
        self.vehicle = make_vehicle(
            self.ag,
            brand,
            model,
            "EXP-9",
            date_assurance=timezone.localdate() - timedelta(days=3),
        )
        generate_alerts_for_agency(self.ag)
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def _titles(self, **headers):
        res = self.api.get("/api/notifications/", **headers)
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        rows = body["results"] if isinstance(body, dict) else body
        return {r["kind"]: r for r in rows}

    def test_default_language_is_french(self):
        row = self._titles()["INSURANCE"]
        self.assertEqual(row["title"], "Assurance expirée - EXP-9")
        self.assertIn("expirée depuis 3 jour(s)", row["message"])

    def test_arabic_when_requested(self):
        row = self._titles(HTTP_ACCEPT_LANGUAGE="ar")["INSURANCE"]
        self.assertEqual(row["title"], "انتهاء التأمين - EXP-9")
        self.assertIn("3", row["message"])

    def test_stored_text_stays_french_for_emails(self):
        n = Notification.objects.get(kind="INSURANCE")
        self.assertEqual(n.code, "INSURANCE_EXPIRED")
        self.assertEqual(n.title, "Assurance expirée - EXP-9")

    def test_old_alert_without_code_falls_back_to_stored_text(self):
        Notification.objects.create(
            agency=self.ag, kind="VIDANGE", title="Ancien titre", message="m", dedup_key="legacy"
        )
        row = self._titles(HTTP_ACCEPT_LANGUAGE="ar")["VIDANGE"]
        self.assertEqual(row["title"], "Ancien titre")
