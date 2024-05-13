import shutil
import time
from copy import copy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import transaction
from django.http import HttpRequest
from django.test import TestCase, override_settings

# Create your tests here.
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware
from selenium import webdriver

from . import tasks
from . import util
from .forms import UploadFileForm
from .models import (
    Route,
    Scenario,
    UploadedFile,
    VehicleType,
    Vehicle,
    Rotation,
    Station,
    Trip,
    Temperatures,
    Event,
    EventType,
    Consumption,
)

TMP_UPLOAD = settings.UPLOAD_PATH + "/temp"
TMP_STATICFILES_DIRS = settings.STATICFILES_DIRS + [settings.BASE_DIR / TMP_UPLOAD]


@override_settings(STATICFILES_DIRS=TMP_STATICFILES_DIRS)
@override_settings(UPLOAD_PATH=TMP_UPLOAD)
@override_settings(SECURE_PROXY_SSL_HEADER=None)
@override_settings(SECURE_SSL_REDIRECT=False)
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

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @override_settings(CELERY_TASK_EAGER_PROPAGATES=True)
    @override_settings(DEBUG=True)
    def test_result_generation_w_celery(self):
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
        time.sleep(2)
        # Clear the browser log. We check the state of the site after refresh, to give
        # map images time to load.
        _ = self.selenium.get_log("browser")
        self.selenium.refresh()
        # give django some time to calculate
        time.sleep(2)
        # Check for 404 requests
        errors = self.selenium.get_log("browser")
        # ToDO handle exception
        # An iframe which has both allow-scripts and allow-same-origin for its sandbox
        # attribute can escape its sandboxing.'
        with self.assertRaises(AssertionError):
            errors = self.assertEqual(len(errors), 0, f"404 errors detected: {errors}")
        allowed_error = (
            "An iframe which has both allow-scripts and allow-same-origin for its "
            "sandbox attribute can escape its sandboxing"
        )
        errors = [error for error in errors if allowed_error not in error["message"]]
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

    first_object = new_objects[0]
    comparable_types = (float, int, str, bool, type(None))

    if isinstance(first_object, comparable_types):
        yield key_stack, new_objects
        return

    if id(first_object) in instance_stack:
        return
    instance_stack.add(id(new_objects[0]))

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


@override_settings(DEBUG=True)
def build_scenario():
    form = UploadFileForm()
    # Use all the initial and set values from the form as post data
    post_data = {
        f: form.fields[f].initial if form.fields[f].initial is not None else "" for f in form.fields
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


class WriteReadScenarioToDatabase(TestCase):
    @override_settings(DEBUG=True)
    def test_schedule_from_database(self):
        """Check if the results are equal if the scenario is run from the form or from the db"""
        django_scenario, simba_schedule, args = build_scenario()
        simba_schedule_db, args_db = tasks.get_schedule_from_db(django_scenario)
        # rotation names and station names are swapped
        rotations_keys = [rot for rot in simba_schedule.rotations]
        db_iter = iter(simba_schedule_db.rotations)

        for rot_id in rotations_keys:
            db_rot_id = next(db_iter)
            db_rot = simba_schedule_db.rotations[db_rot_id]
            simba_schedule.rotations[db_rot_id] = simba_schedule.rotations[rot_id]
            simba_schedule.rotations[db_rot_id].id = db_rot_id
            simba_schedule.rotations[db_rot_id].vehicle_id = db_rot.vehicle_id
            del simba_schedule.rotations[rot_id]

        for sched in [simba_schedule, simba_schedule_db]:
            for rot in sched.rotations.values():
                rot.calculate_consumption()

        for key, value in vars(args).items():
            db_value = vars(args_db).get(key)
            self.assertEqual(db_value, value)

        # Recursively search the schedule for primitive data which has to be equal to the database
        # schedule
        for key_stack, values in objects_digger([simba_schedule, simba_schedule_db]):
            # Skip the temperature data, since it is not part of the database schedule
            self.handle_unaware_datetime(values)
            try:
                self.assertAlmostEqual(
                    values[0],
                    values[1],
                    places=8,
                    msg=key_stack,
                )
            except TypeError:
                raise Exception(f"Could not compare {values[0]} and {values[1]}. {key_stack}")

        scen = simba_schedule.run(args)
        scen_db = simba_schedule_db.run(args_db)

        # Recursively search the scenario for primitive data which has to be equal to the data
        # created by the database schedule
        for key_stack, values in objects_digger([scen, scen_db], early_return=False):
            self.handle_unaware_datetime(values)
            try:
                self.assertAlmostEqual(values[0], values[1], places=8, msg=key_stack)
            except TypeError:
                # assume it's a date. values[0] does not come from database, so it has to be made
                # aware
                values[0] = make_aware(datetime.fromisoformat(values[0]))
                values[1] = datetime.fromisoformat(values[1])
                self.assertAlmostEqual(values[0], values[1], places=8, msg=key_stack)

    def handle_unaware_datetime(self, values):
        if isinstance(values[0], datetime):
            values[0] = make_aware(values[0])

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
        django_scenario, simba_schedule, args = build_scenario()

        # get the schedule and args from the db
        simba_schedule_db, args_db = tasks.get_schedule_from_db(django_scenario)
        # get a vehicle_type which is "used"
        vehicle = Rotation.objects.filter(scenario=django_scenario)[0].vehicle
        vehicle_type = vehicle.vehicle_type
        consumption_table = Consumption.objects.get(vehicle_class__vehicletype=vehicle_type)

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
            (station, "amount_charging_places", 1),
            (station, "power_per_charger", station.power_per_charger * 0.1),
            (station, "power_total", station.power_total * 0.1),
        ]
        if vehicle_type.consumption is not None:
            mutations.append((vehicle_type, "consumption", vehicle_type.consumption * 0.1))
        else:
            mutations.append(
                (consumption_table, "values", [v * 0.1 for v in consumption_table.values])
            )

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
                    self.assertNotEqual(values[0], values[1], msg=key_stack)
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
                    self.assertNotEqual(values[0], values[1], msg=key_stack)
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


@override_settings(SECURE_PROXY_SSL_HEADER=None)
@override_settings(SECURE_SSL_REDIRECT=False)
class RunSimulationTest(StaticLiveServerTestCase):
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @override_settings(CELERY_TASK_EAGER_PROPAGATES=True)
    @override_settings(DEBUG=True)
    def test_submit_button_click_with_celery_with_eflips(self):
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
        vehicle = Vehicle.objects.create(
            name="Test Vehicle", vehicle_type=vehicle_type, scenario=scenario
        )
        self.assertEqual(str(vehicle), "Test Vehicle")

    def test_rotation_creation(self):
        scenario = Scenario.objects.create(name="Test Scenario")
        vehicle_type = VehicleType.objects.create(
            name="Test Type",
            scenario=scenario,
            charging_curve=[[0.0, 150], [1.0, 150]],
            opportunity_charging_capable=True,
            battery_capacity=100,
            charging_efficiency=0.95,
        )
        rotation = Rotation.objects.create(
            name="Test Rotation",
            scenario=scenario,
            allow_opportunity_charging=True,
            vehicle_type=vehicle_type,
        )
        self.assertEqual(str(rotation.name), "Test Rotation")

    def test_station_creation(self):
        scenario = Scenario.objects.create(name="Test Scenario")
        station = Station.objects.create(
            geom="POINT(0 0 0)", name="Test Station", scenario=scenario
        )
        self.assertEqual(str(station.name), "Test Station")

    def test_trip_creation(self):
        scenario = Scenario.objects.create(name="Test Scenario")
        vehicle_type = VehicleType.objects.create(
            name="Test Type",
            scenario=scenario,
            charging_curve=[[0.0, 150], [1.0, 150]],
            opportunity_charging_capable=True,
            battery_capacity=100,
            charging_efficiency=0.95,
        )
        rotation = Rotation.objects.create(
            name="Test Rotation",
            scenario=scenario,
            allow_opportunity_charging=True,
            vehicle_type=vehicle_type,
        )
        departure_station = Station.objects.create(
            geom="POINT(0 0 0)", name="Departure Station", scenario=scenario
        )
        arrival_station = Station.objects.create(
            geom="POINT(1 1 1)", name="Arrival Station", scenario=scenario
        )
        route = Route(
            name="Test Route",
            scenario=scenario,
            departure_station=departure_station,
            arrival_station=arrival_station,
            distance=100,
        )
        route.save()
        trip = Trip.objects.create(
            scenario=scenario,
            rotation=rotation,
            route=route,
            departure_time=parse_datetime("2023-08-14 10:00:00"),
            arrival_time=parse_datetime("2023-08-14 11:00:00"),
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


class ConsumptionTestCase(TestCase):
    def test_get_consumption(self):
        consumption_instance = Consumption.objects.create(
            name="My Consumption",
            columns=["speed", "other"],
            data_points=[[10, 1], [100, 3]],
            values=[1, 2],
        )

        assert consumption_instance.get_consumption({"speed": 10, "other": 1}) == 1
        assert consumption_instance.get_consumption((10, 1)) == 1
        assert consumption_instance.get_consumption({"speed": 100, "other": 3}) == 2
        assert consumption_instance.get_consumption((100, 3)) == 2
        assert consumption_instance.get_consumption((999, 3)) == 2

        c1 = Consumption.objects.create(
            name="My Other Consumption",
            columns=[
                "speed",
            ],
            data_points=[1, 10, 50, 100],
            values=[1, 2, 3, 50],
        )
        c2 = Consumption.objects.create(
            name="My Other Consumption 2",
            columns=[
                "speed",
            ],
            data_points=[[1], [10], [50], [100]],
            values=[1, 2, 3, 50],
        )
        assert c1.get_consumption((1)) == 1 == c2.get_consumption((1))
        assert c1.get_consumption((5.5)) == 1.5 == c2.get_consumption((5.5))
        assert c1.get_consumption((30)) == 2.5 == c2.get_consumption((30))
        assert c1.get_consumption((100)) == 50 == c2.get_consumption((100))
        assert c1.get_consumption((300)) == 50 == c2.get_consumption((300))
        assert c2.nearest_interpolator(99) == 50

        # multidim with more values so linear nd interpol works
        c = Consumption.objects.create(
            name="My Consumption with multidim",
            columns=["speed", "other", "other2"],
            data_points=[
                [10, 20, 1],
                [10, 20, 3],
                [10, 30, 1],
                [10, 30, 3],
                [0, 20, 1],
                [0, 20, 3],
                [0, 30, 1],
                [0, 30, 3],
            ],
            values=[1, 2, 3, 4, 5, 6, 7, 8],
        )
        self.assertAlmostEqual(c.get_consumption((10, 20, 1)), 1)
        self.assertAlmostEqual(c.get_consumption((5, 20, 1)), 3)
        self.assertAlmostEqual(c.get_consumption((10, 20, 2)), 1.5)
        self.assertAlmostEqual(c.get_consumption((10, 25, 3)), 3)
        delta = 1e-9
        self.assertAlmostEqual(c.get_consumption((0 + delta, 30 - delta, 3 - delta)), 8)
        self.assertNotEqual(c.get_consumption((0 + delta, 30 - delta, 3 - delta)), 8)

    def test_model_creation(self):
        consumption_instance = Consumption.objects.create(
            name="My Consumption",
            columns=["speed", "consumption"],
            data_points=[[10, 1], [100, 3]],
            values=[1, 2],
        )

        name = "My other Consumption"
        builder_kwargs = dict(
            name=name,
            columns=["speed", "consumption"],
            data_points=[[10, 1], [100, 3]],
            values=[1, 2],
        )
        # The same consumption name cannot exist twice, except when its bound by a scenario
        Consumption.objects.create(**builder_kwargs)
        assert Consumption.objects.filter(name=name).count() == 1
        with transaction.atomic():
            self.assertRaises(Exception, lambda: Consumption.objects.create(**builder_kwargs))
        assert Consumption.objects.filter(name=name).count() == 1
        # The name can be shared it its associated with a scenario
        s = Scenario.objects.create(name="foo")
        Consumption.objects.create(**builder_kwargs, scenario=s)
        assert Consumption.objects.filter(name=name).count() == 2
        # but only once
        with transaction.atomic():
            self.assertRaises(
                Exception, lambda: Consumption.objects.create(**builder_kwargs, scenario=s)
            )
        assert Consumption.objects.filter(name=name).count() == 2

        # but another scenario can have the same consumption name aswell
        s = Scenario.objects.create(name="bar")
        Consumption.objects.create(**builder_kwargs, scenario=s)
        assert Consumption.objects.filter(name=name).count() == 3

        # Wrong number of input dims
        self.assertRaises(Exception, lambda: consumption_instance.get_consumption((999, 3, 5)))
        # Wrong keys in input dict
        self.assertRaises(
            Exception,
            lambda: consumption_instance.get_consumption({"speesdfd": 100, "consumption": 3}),
        )


class TemperaturesTestCase(TestCase):
    def test_model_creation(self):
        date1 = make_aware(datetime(year=2024, month=1, day=1))
        dt = timedelta(hours=5)
        date2 = date1 + dt
        date3 = date1 - timedelta(days=5)
        temp1 = 25
        temp2 = 0
        temp3 = 100
        t_instance = Temperatures(
            scenario=Scenario.objects.get(pk=Scenario.get_default_pk()),
            name="First Temperatures",
            use_only_time=False,
            datetimes=[date1, date2, date3],
            data=[temp1, temp2, temp3],
        )
        t_instance.save()
        pk = t_instance.pk

        # look up of temperatures using the look up functions
        assert t_instance.get_interpolated_temperature(date1) == temp1
        assert t_instance.get_closest_temperature(date1) == temp1
        assert t_instance.get_interpolated_temperature(date2) == temp2
        assert t_instance.get_closest_temperature(date2) == temp2
        assert t_instance.get_interpolated_temperature(date3) == temp3
        assert t_instance.get_closest_temperature(date3) == temp3
        assert t_instance.get_interpolated_temperature(date1 + dt / 2) == (temp1 + temp2) / 2

        assert t_instance.get_closest_temperature(date1 + dt / 2.01) == temp1
        assert t_instance.get_closest_temperature(date1 + dt / 1.99) == temp2

        # Use only time works if only datetimes from a single date are passed
        t_instance = Temperatures(
            scenario=Scenario.objects.get(pk=Scenario.get_default_pk()),
            name="Second Temperatures",
            use_only_time=True,
            datetimes=[date1, date2],
            data=[temp1, temp2],
        )
        t_instance.save()
        assert t_instance.get_interpolated_temperature(date1) == temp1
        assert t_instance.get_interpolated_temperature(date1 + timedelta(days=1)) == temp1
        assert t_instance.get_interpolated_temperature(date1 + timedelta(days=1) + dt) == temp2

        assert t_instance.get_closest_temperature(date1) == temp1
        assert t_instance.get_closest_temperature(date1 + timedelta(days=1)) == temp1
        assert t_instance.get_closest_temperature(date1 + timedelta(days=1) + dt) == temp2

        # Previous dates get the last possible value
        assert t_instance.get_interpolated_temperature(date3) == temp1

        # new temperature instance will use its own function
        t = Temperatures.objects.get(pk=pk)
        assert t.get_interpolated_temperature(date3) == temp3

        t_instance = Temperatures(
            scenario=Scenario.objects.get(pk=Scenario.get_default_pk()),
            name="Max. Temperatures Berlin",
            use_only_time=True,
            datetimes=[date1, date2, date3],
            data=[temp1, temp2, temp3],
        )
        # Different dates raise an attribute error
        self.assertRaises(AttributeError, t_instance.save)


class TestUtil(TestCase):
    def setUp(self):
        # simple scenario with some events
        scenario = Scenario.objects.create(name="Test")
        vehicle_type = VehicleType.objects.create(
            name="Test Type",
            scenario=scenario,
            charging_curve=[[0, 0], [1, 3]],
            opportunity_charging_capable=False,
            battery_capacity=100,
        )
        vehicle = Vehicle.objects.create(
            scenario=scenario, name="Test Vehicle", vehicle_type=vehicle_type
        )
        r1 = Rotation.objects.create(
            name="Test Rotation 1",
            scenario=scenario,
            vehicle_type=vehicle_type,
            vehicle=vehicle,
            allow_opportunity_charging=True,
        )
        r2 = Rotation.objects.create(
            name="Test Rotation 2",
            scenario=scenario,
            vehicle_type=vehicle_type,
            vehicle=vehicle,
            allow_opportunity_charging=True,
        )
        st = Station.objects.create(geom="POINT(0 0 0)", name="Test Station", scenario=scenario)
        route1 = Route.objects.create(
            distance=120.5,
            name="Main Route",
            name_short="MR",
            scenario=scenario,
            headsign="Downtown",
            departure_station=st,
            arrival_station=st,
        )
        t1 = Trip.objects.create(
            scenario=scenario,
            route=route1,
            rotation=r1,
            departure_time=parse_datetime("2023-01-01 10:00:00+01:00"),
            arrival_time=parse_datetime("2023-01-01 11:00:00+01:00"),
        )
        t2 = Trip.objects.create(
            scenario=scenario,
            route=route1,
            rotation=r2,
            departure_time=parse_datetime("2023-01-01 12:00:00+01:00"),
            arrival_time=parse_datetime("2023-01-01 13:00:00+01:00"),
        )
        Event.objects.create(
            scenario=scenario,
            vehicle=vehicle,
            vehicle_type=vehicle_type,
            station=st,
            time_start=parse_datetime("2023-01-01 09:00:00+01:00"),
            time_end=parse_datetime("2023-01-01 10:00:00+01:00"),
            soc_start=0.5,
            soc_end=0.8,
            event_type=EventType.CHARGING_OPPORTUNITY,
            timeseries={
                "time": [
                    "2023-01-01 09:00:00+01:00",
                    "2023-01-01 09:15:00+01:00",
                    "2023-01-01 09:30:00+01:00",
                    "2023-01-01 09:45:00+01:00",
                    "2023-01-01 10:00:00+01:00",
                ],
                "soc": [0.5, 0.6, 0.7, 0.8, 0.8],
            },
        )
        Event.objects.create(
            scenario=scenario,
            vehicle=vehicle,
            vehicle_type=vehicle_type,
            trip=t1,
            time_start=parse_datetime("2023-01-01 10:00:00+01:00"),
            time_end=parse_datetime("2023-01-01 11:00:00+01:00"),
            soc_start=0.8,
            soc_end=-0.1,
            event_type=EventType.DRIVING,
            timeseries={
                "time": [
                    "2023-01-01 10:00:00+01:00",
                    "2023-01-01 10:15:00+01:00",
                    "2023-01-01 10:30:00+01:00",
                    "2023-01-01 10:45:00+01:00",
                ],
                "soc": [0.8, 0.5, 0.2, -0.1],
            },
        )
        Event.objects.create(
            scenario=scenario,
            vehicle=vehicle,
            vehicle_type=vehicle_type,
            station=st,
            time_start=parse_datetime("2023-01-01 11:00:00+01:00"),
            time_end=parse_datetime("2023-01-01 12:00:00+01:00"),
            soc_start=-0.1,
            soc_end=0.8,
            event_type=EventType.CHARGING_OPPORTUNITY,
            timeseries={
                "time": [
                    "2023-01-01 11:00:00+01:00",
                    "2023-01-01 11:15:00+01:00",
                    "2023-01-01 11:30:00+01:00",
                    "2023-01-01 11:45:00+01:00",
                ],
                "soc": [-0.1, 0.2, 0.5, 0.8],
            },
        )
        Event.objects.create(
            scenario=scenario,
            vehicle=vehicle,
            vehicle_type=vehicle_type,
            trip=t2,
            time_start=parse_datetime("2023-01-01 12:00:00+01:00"),
            time_end=parse_datetime("2023-01-01 13:00:00+01:00"),
            soc_start=0.8,
            soc_end=0.2,
            event_type=EventType.DRIVING,
            timeseries={
                "time": [
                    "2023-01-01 12:00:00+01:00",
                    "2023-01-01 12:15:00+01:00",
                    "2023-01-01 12:30:00+01:00",
                    "2023-01-01 12:45:00+01:00",
                ],
                "soc": [0.8, 0.6, 0.4, 0.2],
            },
        )

    def test_get_soc(self):
        scenario_id = Scenario.objects.get(name="Test").id
        socs = util.get_soc(scenario_id)
        vehicle_id = Vehicle.objects.get(name="Test Vehicle").id
        assert len(socs) == 1  # one vehicle
        assert len(socs[vehicle_id]) == 2  # two times at station

    def test_get_stations(self):
        scenario_id = Scenario.objects.get(name="Test").id
        stations = util.get_stations(scenario_id)
        # TODO change
        assert stations
        # nothing to test yet

    def test_rotation_filter(self):
        scenario_id = Scenario.objects.get(name="Test").id
        rotations = util.rotation_filter(scenario_id)
        r1_id = Rotation.objects.get(name="Test Rotation 1").id
        r2_id = Rotation.objects.get(name="Test Rotation 2").id
        assert r1_id not in rotations  # trip became negative
        assert r2_id in rotations  # all trips positive
