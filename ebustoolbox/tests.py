import time

from django.test import TestCase
from django.utils.dateparse import parse_datetime

# Create your tests here.
from .models import Scenario, UploadedFile, VehicleClass, VehicleType, Vehicle, Rotation, Station, \
    Trip


class ModelTests(TestCase):
    def test_scenario_creation(self):
        scenario = Scenario.objects.create(name='Test Scenario')
        self.assertEqual(str(scenario.name), 'Test Scenario')

    def test_uploaded_file_creation(self):
        scenario = Scenario.objects.create(name='Test Scenario')
        uploaded_file = UploadedFile.objects.create(scenario=scenario, file='test.txt')
        self.assertEqual(str(uploaded_file.file), 'test.txt')

    def test_vehicle_class_creation(self):
        _ = VehicleClass.objects.create(name='Test Class')

    def test_vehicle_type_creation(self):
        scenario = Scenario.objects.create(name='Test Scenario')
        vehicle_class = VehicleClass.objects.create(name='Test Class')
        vehicle_type = VehicleType.objects.create(
            name='Test Type',
            scenario=scenario,
            vehicle_class=vehicle_class,
            charging_curve=[[0, 0], [1, 3]],
            flex_charging=True,
            battery_capacity=100,
            charging_efficiency=0.95
        )
        self.assertEqual(str(vehicle_type.name), 'Test Type')

    def test_vehicle_creation(self):
        scenario = Scenario.objects.create(name='Test Scenario')
        vehicle_class = VehicleClass.objects.create(name='Test Class')
        vehicle_type = VehicleType.objects.create(
            name='Test Type',
            scenario=scenario,
            vehicle_class=vehicle_class,
            charging_curve=[[0, 0], [1, 3]],
            flex_charging=True,
            battery_capacity=100,
            charging_efficiency=0.95
        )
        vehicle = Vehicle.objects.create(name='Test Vehicle', vehicle_type=vehicle_type,
                                         scenario=scenario)
        self.assertEqual(str(vehicle), 'Test Vehicle')

    def test_rotation_creation(self):
        vehicle_class = VehicleClass.objects.create(name='Test Class')
        scenario = Scenario.objects.create(name='Test Scenario')
        rotation = Rotation.objects.create(name='Test Rotation', vehicle_class=vehicle_class,
                                           scenario=scenario)
        self.assertEqual(str(rotation.name), 'Test Rotation')

    def test_station_creation(self):
        scenario = Scenario.objects.create(name='Test Scenario')
        station = Station.objects.create(geom='POINT(0 0 0)', name='Test Station',
                                         scenario=scenario)
        self.assertEqual(str(station.name), 'Test Station')

    def test_trip_creation(self):
        vehicle_class = VehicleClass.objects.create(name='Test Class')
        scenario = Scenario.objects.create(name='Test Scenario')
        rotation = Rotation.objects.create(name='Test Rotation', vehicle_class=vehicle_class,
                                           scenario=scenario)
        departure_station = Station.objects.create(geom='POINT(0 0 0)', name='Departure Station',
                                                   scenario=scenario)
        arrival_station = Station.objects.create(geom='POINT(1 1 1)', name='Arrival Station',
                                                 scenario=scenario)
        trip = Trip.objects.create(
            rotation=rotation,
            departure_stop=departure_station,
            departure_time=parse_datetime('2023-08-14 10:00:00'),
            arrival_stop=arrival_station,
            arrival_time=parse_datetime('2023-08-14 11:00:00'),
            distance=100
        )
        self.assertEqual(trip.duration_in_seconds, 3600)
        self.assertEqual(trip.incline, 0.01)
        self.assertEqual(trip.speed, 100 / 3600)


class ScenarioTestCase(TestCase):
    def setUp(self):
        # Create test instances of your model
        Scenario.objects.create(name='Instance 1')
        time.sleep(0.01)
        Scenario.objects.create(name='Instance 2')

    def test_model_creation(self):
        instance_1 = Scenario.objects.get(name='Instance 1')
        instance_2 = Scenario.objects.get(name='Instance 2')
        self.assertGreater(instance_2.created, instance_1.created)
        self.assertIsNone(instance_1.finished)
        self.assertIsInstance(instance_1.options, dict)
        self.assertIsNone(instance_1.task_id)
