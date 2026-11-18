from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from agency.models import CustomUser, Notification
from agency.notifications import run_alerts
from fleet.geofence import check_geofences, haversine_m, vehicles_outside
from fleet.models import Geofence, GpsDevice
from payments.test_invoicing import setup_agency

# Casablanca ~ (33.5731, -7.5898) ; Rabat ~ (34.0209, -6.8416) : environ 87 km
CASA = (33.5731, -7.5898)
RABAT = (34.0209, -6.8416)


def position(device_id, lat, lon):
    return {
        "deviceId": device_id,
        "latitude": lat,
        "longitude": lon,
        "fixTime": "2026-11-18T10:00:00.000+00:00",
        "attributes": {},
    }


class HaversineTests(TestCase):
    def test_known_distance(self):
        km = haversine_m(*CASA, *RABAT) / 1000
        self.assertAlmostEqual(km, 87, delta=5)

    def test_zero_distance(self):
        self.assertEqual(haversine_m(*CASA, *CASA), 0)


class GeofenceCheckTests(TestCase):
    def setUp(self):
        self.ag, self.owner, self.contract = setup_agency("GF")
        self.contract.statut = "EN_COURS"
        self.contract.save()
        self.vehicle = self.contract.vehicle
        GpsDevice.objects.create(
            agency=self.ag, vehicle=self.vehicle, traccar_device_id=77
        )
        # Zone autorisée : 30 km autour de Casablanca
        self.zone = Geofence.objects.create(
            agency=self.ag,
            name="Casablanca",
            latitude=CASA[0],
            longitude=CASA[1],
            radius_m=30_000,
        )

    def test_vehicle_inside_zone_is_fine(self):
        self.assertEqual(
            vehicles_outside(self.ag, positions={77: position(77, *CASA)}), []
        )

    def test_vehicle_outside_zone_is_reported_with_distance(self):
        out = vehicles_outside(self.ag, positions={77: position(77, *RABAT)})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["contract_id"], self.contract.id)
        self.assertEqual(out[0]["nearest_zone"], "Casablanca")
        self.assertGreater(out[0]["distance_outside_m"], 40_000)

    def test_no_active_zone_means_no_control(self):
        self.zone.is_active = False
        self.zone.save()
        self.assertEqual(
            vehicles_outside(self.ag, positions={77: position(77, *RABAT)}), []
        )

    def test_inside_any_zone_is_enough(self):
        Geofence.objects.create(
            agency=self.ag,
            name="Rabat",
            latitude=RABAT[0],
            longitude=RABAT[1],
            radius_m=20_000,
        )
        self.assertEqual(
            vehicles_outside(self.ag, positions={77: position(77, *RABAT)}), []
        )

    def test_only_rented_vehicles_are_controlled(self):
        self.contract.statut = "TERMINE"
        self.contract.save()
        self.assertEqual(
            vehicles_outside(self.ag, positions={77: position(77, *RABAT)}), []
        )

    def test_missing_or_invalid_gps_fix_is_ignored(self):
        self.assertEqual(vehicles_outside(self.ag, positions={}), [])
        self.assertEqual(
            vehicles_outside(self.ag, positions={77: position(77, 0, 0)}), []
        )

    def test_alert_created_once_per_contract_per_day(self):
        positions = {77: position(77, *RABAT)}
        first = check_geofences(self.ag, positions=positions)
        again = check_geofences(self.ag, positions=positions)
        self.assertEqual(len(first), 1)
        self.assertEqual(again, [])
        notif = Notification.objects.get(kind="GEOFENCE")
        self.assertEqual(notif.level, "CRITICAL")
        self.assertIn(self.vehicle.matricule, notif.title)
        self.assertEqual(notif.contract_id, self.contract.id)

    def test_traccar_not_configured_does_not_break_alerts(self):
        from fleet.traccar import TraccarNotConfigured

        with mock.patch(
            "fleet.geofence.positions_by_device", side_effect=TraccarNotConfigured()
        ):
            self.assertEqual(vehicles_outside(self.ag), [])
            self.assertEqual(check_geofences(self.ag), [])

    def test_run_alerts_includes_geofence_alerts(self):
        with mock.patch(
            "fleet.geofence.positions_by_device",
            return_value={77: position(77, *RABAT)},
        ):
            run_alerts(send_email=False, agencies=[self.ag])
        self.assertTrue(
            Notification.objects.filter(agency=self.ag, kind="GEOFENCE").exists()
        )


class GeofenceApiTests(TestCase):
    def setUp(self):
        self.ag, self.owner, self.contract = setup_agency("GA")
        self.employee = CustomUser.objects.create_user(
            "emp_ga", password="pw12345", agency=self.ag, role="EMPLOYEE"
        )
        self.api = APIClient()
        self.api.force_authenticate(self.owner)
        self.payload = {
            "name": "Casa",
            "latitude": 33.57,
            "longitude": -7.59,
            "radius_m": 25000,
        }

    def test_owner_creates_and_lists_zones(self):
        res = self.api.post("/api/geofences/", self.payload, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        listing = self.api.get("/api/geofences/").json()
        self.assertEqual([z["name"] for z in listing], ["Casa"])

    def test_employee_can_read_but_not_write(self):
        self.api.post("/api/geofences/", self.payload, format="json")
        emp = APIClient()
        emp.force_authenticate(self.employee)
        self.assertEqual(emp.get("/api/geofences/").status_code, 200)
        self.assertEqual(
            emp.post("/api/geofences/", self.payload, format="json").status_code, 403
        )
        zone_id = self.api.get("/api/geofences/").json()[0]["id"]
        self.assertEqual(emp.delete(f"/api/geofences/{zone_id}/").status_code, 403)

    def test_validation_of_coordinates_and_radius(self):
        for bad in (
            {**self.payload, "latitude": 91},
            {**self.payload, "longitude": -181},
            {**self.payload, "radius_m": 10},
            {**self.payload, "radius_m": 5_000_000},
        ):
            self.assertEqual(
                self.api.post("/api/geofences/", bad, format="json").status_code, 400
            )

    def test_other_agency_zones_are_invisible(self):
        self.api.post("/api/geofences/", self.payload, format="json")
        _, other_owner, _ = setup_agency("GB")
        other = APIClient()
        other.force_authenticate(other_owner)
        self.assertEqual(other.get("/api/geofences/").json(), [])
        zone_id = self.api.get("/api/geofences/").json()[0]["id"]
        self.assertEqual(other.delete(f"/api/geofences/{zone_id}/").status_code, 404)

    def test_status_endpoint_lists_vehicles_outside(self):
        self.contract.statut = "EN_COURS"
        self.contract.save()
        GpsDevice.objects.create(
            agency=self.ag, vehicle=self.contract.vehicle, traccar_device_id=5
        )
        self.api.post("/api/geofences/", self.payload, format="json")
        with mock.patch(
            "fleet.geofence.positions_by_device", return_value={5: position(5, *RABAT)}
        ):
            res = self.api.get("/api/geofences/status/")
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["zones"], 1)
        self.assertEqual(len(body["outside"]), 1)
