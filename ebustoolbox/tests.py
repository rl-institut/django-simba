import time

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import TestCase, override_settings
from django.utils.dateparse import parse_datetime
from .forms import UploadFileForm

# Create your tests here.
from django.urls import reverse
from selenium import webdriver

from ebustoolbox import tasks
from .models import (
    Scenario,
    UploadedFile,
    VehicleClass,
    VehicleType,
    Vehicle,
    Rotation,
    Station,
    Trip,
)


class MySeleniumTests(StaticLiveServerTestCase):
    """Note running this is debug does not seem to work"""

    # fixtures = ["user-data.json"]
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.selenium = webdriver.Chrome()
        cls.selenium.implicitly_wait(10)

    @classmethod
    def tearDownClass(cls):
        cls.selenium.quit()
        super().tearDownClass()

    @override_settings(DEBUG=True)
    def test_result_generation(self):
        # Get the URL using reverse
        url = reverse("simba:home")

        # Simulate a GET request to the URL
        response = self.client.get(url)

        # Check response status code (200 OK)
        self.assertEqual(response.status_code, 200)

        # Check if the button is present in the response content
        self.assertContains(response, "simba_submit_button", html=False)
        form = UploadFileForm()
        # Use all the initial and set values from the form as post data
        post_data = {
            f: form.fields[f].initial if form.fields[f].initial is not None else ""
            for f in form.fields
        }
        # Simulate clicking the button (POST request)
        response = self.client.post(url, post_data)

        # Check response status code. Have you been redirected
        self.assertEqual(response.status_code, 302)
        url = response.url
        response = self.client.get(url)
        self.selenium.get(f"{self.live_server_url}{url}")
        # Check for 404 requests
        errors = self.selenium.get_log("browser")
        self.assertEqual(len(errors), 0, f"404 errors detected: {errors}")


class MyViewTest(TestCase):
    @override_settings(CELERY_BROKER_URL="pyamqp://guest@localhost//")
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @override_settings(DEBUG=True)
    def test_submit_button_click_with_celery(self):
        self.submit_default_simulation()

    @override_settings(CELERY_BROKER_URL=None)
    @override_settings(DEBUG=True)
    def test_submit_button_click_without_celery(self):
        self.submit_default_simulation()

    def submit_default_simulation(self):
        # Get the URL using reverse
        url = reverse("simba:home")
        # Simulate a GET request to the URL
        response = self.client.get(url)
        # Check response status code (200 OK)
        self.assertEqual(response.status_code, 200)
        # Check if the button is present in the response content
        self.assertContains(response, "simba_submit_button", html=False)
        form = UploadFileForm()
        # Use all the initial and set values from the form as post data
        post_data = {
            f: form.fields[f].initial if form.fields[f].initial is not None else ""
            for f in form.fields
        }
        # Simulate clicking the button (POST request)
        response = self.client.post(url, post_data)
        # Check response status code. Have you been redirected
        self.assertEqual(response.status_code, 302)
        response = self.client.get(response.url)
        self.assertEqual(response.status_code, 200)


class ModelTests(TestCase):
    def test_scenario_creation(self):
        scenario = Scenario.objects.create(name="Test Scenario")
        self.assertEqual(str(scenario.name), "Test Scenario")

    def test_uploaded_file_creation(self):
        scenario = Scenario.objects.create(name="Test Scenario")
        uploaded_file = UploadedFile.objects.create(scenario=scenario, file="test.txt")
        self.assertEqual(str(uploaded_file.file), "test.txt")

    def test_vehicle_class_creation(self):
        _ = VehicleClass.objects.create(name="Test Class")

    def test_vehicle_type_creation(self):
        scenario = Scenario.objects.create(name="Test Scenario")
        vehicle_class = VehicleClass.objects.create(name="Test Class")
        vehicle_type = VehicleType.objects.create(
            name="Test Type",
            scenario=scenario,
            vehicle_class=vehicle_class,
            charging_curve=[[0, 0], [1, 3]],
            flex_charging=True,
            battery_capacity=100,
            charging_efficiency=0.95,
        )
        self.assertEqual(str(vehicle_type.name), "Test Type")

    def test_vehicle_creation(self):
        scenario = Scenario.objects.create(name="Test Scenario")
        vehicle_class = VehicleClass.objects.create(name="Test Class")
        vehicle_type = VehicleType.objects.create(
            name="Test Type",
            scenario=scenario,
            vehicle_class=vehicle_class,
            charging_curve=[[0, 0], [1, 3]],
            flex_charging=True,
            battery_capacity=100,
            charging_efficiency=0.95,
        )
        vehicle = Vehicle.objects.create(
            name="Test Vehicle", vehicle_type=vehicle_type, scenario=scenario
        )
        self.assertEqual(str(vehicle), "Test Vehicle")

    def test_rotation_creation(self):
        vehicle_class = VehicleClass.objects.create(name="Test Class")
        scenario = Scenario.objects.create(name="Test Scenario")
        rotation = Rotation.objects.create(
            name="Test Rotation", vehicle_class=vehicle_class, scenario=scenario
        )
        self.assertEqual(str(rotation.name), "Test Rotation")

    def test_station_creation(self):
        scenario = Scenario.objects.create(name="Test Scenario")
        station = Station.objects.create(
            geom="POINT(0 0 0)", name="Test Station", scenario=scenario
        )
        self.assertEqual(str(station.name), "Test Station")

    def test_trip_creation(self):
        vehicle_class = VehicleClass.objects.create(name="Test Class")
        scenario = Scenario.objects.create(name="Test Scenario")
        rotation = Rotation.objects.create(
            name="Test Rotation", vehicle_class=vehicle_class, scenario=scenario
        )
        departure_station = Station.objects.create(
            geom="POINT(0 0 0)", name="Departure Station", scenario=scenario
        )
        arrival_station = Station.objects.create(
            geom="POINT(1 1 1)", name="Arrival Station", scenario=scenario
        )
        trip = Trip.objects.create(
            rotation=rotation,
            departure_stop=departure_station,
            departure_time=parse_datetime("2023-08-14 10:00:00"),
            arrival_stop=arrival_station,
            arrival_time=parse_datetime("2023-08-14 11:00:00"),
            distance=100,
        )
        self.assertEqual(trip.duration_in_seconds, 3600)
        self.assertEqual(trip.incline, 0.01)
        self.assertEqual(trip.speed, 100 / 3600)


class ScenarioTestCase(TestCase):
    def setUp(self):
        # Create test instances of your model
        Scenario.objects.create(name="Instance 1")
        time.sleep(0.01)
        Scenario.objects.create(name="Instance 2")

    def test_model_creation(self):
        instance_1 = Scenario.objects.get(name="Instance 1")
        instance_2 = Scenario.objects.get(name="Instance 2")
        self.assertGreater(instance_2.created, instance_1.created)
        self.assertIsNone(instance_1.finished)
        self.assertIsInstance(instance_1.options, dict)
        self.assertIsNone(instance_1.task_id)
