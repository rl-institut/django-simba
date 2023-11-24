import shutil
import time
from datetime import datetime
from pathlib import Path
from copy import copy
from typing import Iterable

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.http import HttpRequest
from django.test import TestCase, override_settings
from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware

from . import tasks
from .forms import UploadFileForm
from django.conf import settings

# Create your tests here.
from django.urls import reverse
from selenium import webdriver

from .models import (
    Scenario,
    UploadedFile,
    VehicleType,
    Vehicle,
    Rotation,
    Station,
    Trip,
)

TMP_UPLOAD = settings.UPLOAD_PATH + "/temp"
TMP_STATICFILES_DIRS = settings.STATICFILES_DIRS + [settings.BASE_DIR / TMP_UPLOAD]


@override_settings(STATICFILES_DIRS=TMP_STATICFILES_DIRS)
@override_settings(UPLOAD_PATH=TMP_UPLOAD)
class MySeleniumTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        options_ = webdriver.chrome.options.Options()
        options_.add_argument("--headless=new")
        cls.selenium = webdriver.Chrome(options=options_)
        cls.selenium.implicitly_wait(10)
        Path(TMP_UPLOAD).mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.selenium.quit()
        super().tearDownClass()
        shutil.rmtree(TMP_UPLOAD)

    @override_settings(CELERY_USE=True)
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @override_settings(DEBUG=True)
    def test_result_generation_w_celery(self):
        self.simple_simba_call_in_selenium()

    @override_settings(CELERY_USE=False)
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @override_settings(DEBUG=True)
    def test_result_generation(self):
        self.simple_simba_call_in_selenium()

    def simple_simba_call_in_selenium(self):
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
        # give django some time to calculate
        time.sleep(3)
        # Check for 404 requests
        errors = self.selenium.get_log("browser")
        self.assertEqual(len(errors), 0, f"404 errors detected: {errors}")
        self.assertContains(response, "Finished")


def castable_to_dict(objects: Iterable):
    if isinstance(next(iter(objects)), dict):
        return True
    try:
        [vars(o) for o in objects]
    except TypeError:
        return False
    return True


def cast_to_dict(objects: Iterable):
    if isinstance(next(iter(objects)), dict):
        return objects
    return [vars(obj) for obj in objects]


def objects_digger(objects, early_return=True, key_stack=None, instance_stack=None):
    """Digs through objects and yields key_stack and 'primitive' data suited for comparison

    The key_stack contains the keys of objects along the object path, e.g. an object
    containing an object foo, which contains an attribute bar which is a string "Baz" would yield
    [foo, bar] "baz"
    if multiple objects are passed all the values for foo.bar are yielded. Infinite recursion is
    handled through an instance stack which keeps track of instances which were searched already.
    each instance can only be searched once.
    """
    if key_stack is None:
        key_stack = []
    if instance_stack is None:
        instance_stack = set()

    new_objects = [o for o in objects]

    if id(new_objects[0]) in instance_stack:
        return
    instance_stack.add(id(new_objects[0]))

    first_object = new_objects[0]
    comparable_types = (float, int, str, bool)

    if isinstance(first_object, comparable_types):
        yield key_stack, new_objects
        return

    # new_objects are
    key_stack.append(None)

    dict_like = castable_to_dict(new_objects)
    if dict_like:
        dict_objects = cast_to_dict(new_objects)
        yield from dict_digger(early_return, instance_stack, key_stack, dict_objects)
        return

    list_like = isinstance(first_object, Iterable) and not dict_like
    if list_like:
        if early_return and isinstance(next(iter(first_object)), comparable_types):
            yield key_stack, new_objects
        else:
            yield from list_digger(early_return, instance_stack, key_stack, new_objects)
        return

    # if the new object is neither dict_like nor iterabl, it is some kind of leaf type, which was
    # not recognized as comparable_type. It is yielded here, and has to be handled.
    if not dict_like and not list_like:
        yield key_stack, new_objects


def list_digger(early_return, instance_stack, key_stack, new_objects):
    key_stack_copy = key_stack.copy()
    list_objects = []
    for obj in new_objects:
        list_objects.append(list(obj))
    for i, list_element in enumerate(list_objects[0]):
        try:
            key_stack_copy = [key for key in key_stack]
            key_stack_copy[-1] = i
            inner_objects = [o[i] for o in list_objects]
            for x in objects_digger(
                inner_objects,
                early_return=early_return,
                key_stack=key_stack_copy,
                instance_stack=instance_stack,
            ):
                yield x
        except IndexError:
            print("Early return due to lists of different length")
            print(key_stack_copy)
            yield key_stack_copy, new_objects
            break


def dict_digger(early_return, instance_stack, key_stack, new_objects):
    for key, value in new_objects[0].items():
        inner_objects = [o[key] for o in new_objects]
        key_stack_copy = [key for key in key_stack]
        key_stack_copy[-1] = key
        for x in objects_digger(
            inner_objects,
            early_return=early_return,
            key_stack=key_stack_copy,
            instance_stack=instance_stack,
        ):
            yield x


class WriteReadScenarioToDatabase(TestCase):
    @override_settings(DEBUG=True)
    def get_scenario_objects_and_fill_db(self):
        form = UploadFileForm()
        # Use all the initial and set values from the form as post data
        post_data = {
            f: form.fields[f].initial if form.fields[f].initial is not None else ""
            for f in form.fields
        }
        # create form with post data without extra files
        form = UploadFileForm(data=post_data, files=None)
        form.full_clean()

        # Empty request, since no files are used for this simulation.
        request = HttpRequest()

        django_scenario, simba_schedule, args = tasks.input_files_to_database(
            form.cleaned_data, request
        )
        return django_scenario, simba_schedule, args

    @override_settings(DEBUG=True)
    def test_schedule_from_database(self):
        django_scenario, simba_schedule, args = self.get_scenario_objects_and_fill_db()

        # simba_schedule_db, args_db = tasks.db_to_schedule(django_scenario)
        simba_schedule_db, args_db = tasks.get_schedule_from_db(django_scenario)

        for sched in [simba_schedule, simba_schedule_db]:
            for rot in sched.rotations.values():
                rot.calculate_consumption()
        for key, value in vars(args).items():
            # Some values don't need to be part of the args. Relative and absolute Paths are also
            # ignored
            if key in ["electrified_stations", "vehicle_types"]:
                continue
            db_value = vars(args_db).get(key)
            self.assertEqual(db_value, value)

        # Recursively search the schedule for primitive data which has to be equal to the database
        # schedule
        for key_stack, values in objects_digger([simba_schedule, simba_schedule_db]):
            if isinstance(values[0], datetime):
                values[0] = make_aware(values[0])
            self.assertAlmostEqual(
                values[0],
                values[1],
                places=8,
                msg=key_stack,
            )

        scen = simba_schedule.run(args)
        scen_db = simba_schedule_db.run(args_db)
        # Recursively search the scenario for primitive data which has to be equal to the data
        # created by the database schedule
        for key_stack, values in objects_digger([scen, scen_db], early_return=False):
            ignore_key = False
            for key in ["electrified_stations", "vehicle_types"]:
                if key in key_stack:
                    ignore_key = True
            if ignore_key:
                continue
            if isinstance(values[0], datetime):
                values[0] = make_aware(values[0])
            try:
                self.assertAlmostEquals(values[0], values[1], places=8, msg=key_stack)
            except TypeError:
                # assume it's a date. values[0] does not come from database, so it has to be made
                # aware
                values[0] = make_aware(datetime.fromisoformat(values[0]))
                values[1] = datetime.fromisoformat(values[1])
                self.assertAlmostEquals(values[0], values[1], places=8, msg=key_stack)

    # Above code shows "normal" and database schedule seem to generate the same output.
    # Test if the opposite is true by changing database values. Each change of a database
    # value has to lead to differing outputs
    def testDatabaseEffects(self):
        """Test if a change in the database values results in changes in the schedule and scenario

        The database could contain data which has no effect on the simulation. To make sure the data
        is used in the simulation, the database is changed and the resulting schedule and scenario
        are compared to the original unchanged schedule and scenario. The test fails if the tested
        variations do not lead to differences. The differences are not checked for plausibility but
        only for their occurrence.
        """
        # create a scenario from the form
        django_scenario, simba_schedule, args = self.get_scenario_objects_and_fill_db()

        # get the schedule and args from the db
        simba_schedule_db, args_db = tasks.get_schedule_from_db(django_scenario)
        # get a vehicle_type which is "used"
        vehicle = Rotation.objects.filter(scenario=django_scenario)[0].vehicle
        vehicle_type = vehicle.vehicle_type

        station = Station.objects.get(scenario=django_scenario, name="Station-0")
        # mutate with instance, field name, value
        mutations = [
            (vehicle_type, "battery_capacity", 1),
            (vehicle_type, "charging_efficiency", 0.1),
            (vehicle_type, "minimum_charging_power", vehicle_type.charging_curve[0][1] * 0.99),
            (
                vehicle_type,
                "charging_curve",
                [[x[0], x[1] * 0.1] for x in vehicle_type.charging_curve],
            ),
            (vehicle_type, "consumption", vehicle_type.consumption * 0.1),
            (station, "amount_charging_places", 1),
            (station, "power_per_charger", station.power_per_charger * 0.1),
            (station, "power_total", station.power_total * 0.1),
        ]
        scen_db = simba_schedule_db.run(args_db)
        # running the schedule changes the schedule since it assigns vehicles. therefore load it
        # again to have a "vanilla" schedule
        original_database_schedule, original_db_args = tasks.get_schedule_from_db(django_scenario)

        # Now the database is mutated in various ways.
        # Each mutation must lead to a difference in the resulting schedule and scenario.
        # Both of these are checked against the original objects from above
        for mutation in mutations:
            instance = copy(mutation[0])
            vars(instance).update(dict(((mutation[1], mutation[2]),)))
            instance.save()
            mut_simba_schedule, args_db = tasks.get_schedule_from_db(django_scenario)
            # restore original value in case the instance changes
            instance = copy(mutation[0])
            instance.save()

            # Recursively search the schedule for primitive data which has to be not equal to the
            # database schedule in at least ONE case
            difference_found = False
            difference = None
            for key_stack, values in objects_digger(
                [original_database_schedule, mut_simba_schedule]
            ):
                try:
                    self.assertNotEquals(values[0], values[1], msg=key_stack)
                    difference = key_stack, values
                    difference_found = True
                    break
                except AssertionError:
                    # not every value has to differ
                    pass
            if not difference_found:
                raise AssertionError(
                    f"The Schedule read from the database does not diverge from the original one, "
                    f"although changes to the database were made. The mutation was: {mutation}"
                )
            else:
                print(f"Difference in schedule was successfully found for {mutation}, {difference}")
            mut_scen = mut_simba_schedule.run(args_db)
            # Recursively search the scenario for primitive data which has to be NOT equal to the
            # data created by the database schedule
            difference_found = False
            for key_stack, values in objects_digger([mut_scen, scen_db], early_return=False):
                ignore_key = False
                for key in ["electrified_stations", "vehicle_types"]:
                    if key in key_stack:
                        ignore_key = True
                if ignore_key:
                    continue
                try:
                    self.assertNotEquals(values[0], values[1], msg=key_stack)
                    difference = key_stack, values
                    difference_found = True
                    break
                except AssertionError:
                    # not every value has to differ
                    pass
            if not difference_found:
                raise AssertionError(
                    "The Schedule read from the database does not diverge from "
                    "the original one although changes to the database were made. "
                    f"The mutation was: {mutation}"
                )
            else:
                print(f"Difference in scenario was found for {mutation}, {difference}")


class RunSimulationTest(TestCase):
    @override_settings(CELERY_USE=True)
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @override_settings(DEBUG=True)
    def test_submit_button_click_with_celery(self):
        self.submit_default_simulation()

    @override_settings(CELERY_USE=False)
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

    def test_vehicle_type_creation(self):
        scenario = Scenario.objects.create(name="Test Scenario")
        vehicle_type = VehicleType.objects.create(
            name="Test Type",
            scenario=scenario,
            charging_curve=[[0, 0], [1, 3]],
            opportunity_charging_capable=True,
            battery_capacity=100,
            charging_efficiency=0.95,
        )
        self.assertEqual(str(vehicle_type.name), "Test Type")

    def test_vehicle_creation(self):
        scenario = Scenario.objects.create(name="Test Scenario")
        vehicle_type = VehicleType.objects.create(
            name="Test Type",
            scenario=scenario,
            charging_curve=[[0, 0], [1, 3]],
            opportunity_charging_capable=True,
            battery_capacity=100,
            charging_efficiency=0.95,
        )
        vehicle = Vehicle.objects.create(name="Test Vehicle", vehicle_type=vehicle_type)
        self.assertEqual(str(vehicle), "Test Vehicle")

    def test_rotation_creation(self):
        scenario = Scenario.objects.create(name="Test Scenario")
        rotation = Rotation.objects.create(name="Test Rotation", scenario=scenario)
        self.assertEqual(str(rotation.name), "Test Rotation")

    def test_station_creation(self):
        scenario = Scenario.objects.create(name="Test Scenario")
        station = Station.objects.create(
            geom="POINT(0 0 0)", name="Test Station", scenario=scenario
        )
        self.assertEqual(str(station.name), "Test Station")

    def test_trip_creation(self):
        scenario = Scenario.objects.create(name="Test Scenario")
        rotation = Rotation.objects.create(name="Test Rotation", scenario=scenario)
        departure_station = Station.objects.create(
            geom="POINT(0 0 0)", name="Departure Station", scenario=scenario
        )
        arrival_station = Station.objects.create(
            geom="POINT(1 1 1)", name="Arrival Station", scenario=scenario
        )
        trip = Trip.objects.create(
            rotation=rotation,
            departure_station=departure_station,
            departure_time=parse_datetime("2023-08-14 10:00:00"),
            arrival_station=arrival_station,
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
        self.assertIsInstance(instance_1.simba_options, dict)
        self.assertIsNone(instance_1.task_id)
