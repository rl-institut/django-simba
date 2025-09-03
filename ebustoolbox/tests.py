import time
from copy import copy
from datetime import datetime, timedelta
from typing import Iterable
import io
import zipfile

import pandas as pd
from django.conf import settings
from django.db.models import F
from django.http import HttpRequest
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

import django.apps
import core.deepcopy

# Create your tests here.
from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware

from ebustoolbox.schedule_readers import SimbaScheduleReader

from .import_export import visit_all_scenario_queries, ScenarioJSONImporterExporter
from . import tasks
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
    VehicleClass,
    EnumVoltageLevel,
    EnumChargeType,
)
from .tasks import run_simba_scenario
from .util import get_unique_task_id, validate_zip, ZipFileException


TMP_UPLOAD = settings.UPLOAD_PATH + "/temp"
TMP_STATICFILES_DIRS = settings.STATICFILES_DIRS + [settings.BASE_DIR / TMP_UPLOAD]


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

    for station in Station.objects.filter(scenario=django_scenario):
        if station.amount_charging_places is None:
            station.amount_charging_places = 1
        if station.power_per_charger is None:
            station.power_per_charger = django_scenario.simba_options["cs_power_opps"]
        if station.power_total is None:
            station.power_total = django_scenario.simba_options["gc_power_opps"]
        station.save()
    return django_scenario, simba_schedule, args


class WriteReadScenarioToDatabase(TestCase):
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
        vehicle = Rotation.objects.filter(scenario=django_scenario).first().vehicle
        vehicle_type = vehicle.vehicle_type
        consumption_table = Consumption.objects.get(vehicle_class__vehicletype=vehicle_type)

        station = Station.objects.get(scenario=django_scenario, name="Station-0")
        vehicle_type.charging_curve[1][1] = vehicle_type.charging_curve[0][1] * 0.8
        vehicle_type.save()

        # mutate with instance, field name, value
        mutations = [
            (vehicle_type, "battery_capacity", 1),
            (vehicle_type, "charging_efficiency", 0.1),
            (
                vehicle_type,
                "minimum_charging_power",
                vehicle_type.charging_curve[0][1] * 0.99,
            ),
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
                (
                    consumption_table,
                    "values",
                    [v * 0.1 for v in consumption_table.values],
                )
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
            for key_stack, values in objects_digger(
                [original_database_schedule, mut_simba_schedule]
            ):
                try:
                    self.assertNotEqual(values[0], values[1], msg=key_stack)
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


class SimulationTestCase(TransactionTestCase):
    def create_depot_simulation(self):
        # PROBLEM: test scenario has no CHARGING_DEPOT events, just DRIVING and STANDBY_DEPARTURE
        # => can't test depot charging with this
        # HACK: change long STANDBY_DEPARTURE to CHARGING_DEPOT events and all stations to depots
        django_scenario, simba_schedule, args = build_scenario()
        django_scenario.simba_options = {"HORIZON": 1, "perfect_foresight": False}
        django_scenario.save(update_fields=["simba_options"])
        # run scenario, create events
        run_simba_scenario(django_scenario=django_scenario)
        django_scenario.event_set.filter(
            event_type=EventType.STANDBY_DEPARTURE,
            # only events long enough for charging
            time_start__lte=F("time_end") - timedelta(minutes=30),
        ).update(
            event_type=EventType.CHARGING_DEPOT,
            soc_end=1.0,
        )
        django_scenario.station_set.update(
            is_electrified=True, charge_type=EnumChargeType.DEPOT, voltage_level="MV"
        )
        return django_scenario

    def test_flex_band_off(self):
        django_scenario, simba_schedule, args = build_scenario()
        args.skip_flex_report = False
        django_scenario.options = vars(args)
        django_scenario.save()
        django_scenario.refresh_from_db()
        # switching the flex_report_on does not work
        assert django_scenario.options["skip_flex_report"] is False
        # Even though the flag is set in the database, simba will ignore it
        simba_schedule, simba_scenario = run_simba_scenario(django_scenario)
        # no flex_band calculations are found
        assert simba_scenario.flex_bands is None, "Flex bands should be turned off"

    def test_simulate_depot_strategy(self):
        django_scenario = self.create_depot_simulation()
        spiceev_scenario_dict = tasks.create_spiceev_scenario_dict(django_scenario)
        # unknown strategy
        with self.assertRaises(NotImplementedError):
            tasks.simulate_depot_strategy(spiceev_scenario_dict, "error")

        # simulate with SpiceEV strategies
        for strategy in ("greedy", "balanced", "peak_shaving"):
            spiceev_scenario = tasks.simulate_depot_strategy(spiceev_scenario_dict, strategy)
            self.assertEqual(spiceev_scenario.step_i, spiceev_scenario.n_intervals)

    def test_replace_event_timeseries(self):
        django_scenario = self.create_depot_simulation()
        event = (
            django_scenario.event_set.filter(event_type=EventType.CHARGING_DEPOT)
            .order_by("id")
            .last()
        )
        num_ts = len(event.timeseries["time"])
        # check start/end soc
        assert event.soc_start != event.soc_end
        with self.assertRaises(AssertionError):
            tasks.replace_event_timeseries(event, [event.soc_start] * num_ts)
        with self.assertRaises(AssertionError):
            tasks.replace_event_timeseries(event, [event.soc_end] * num_ts)

        # check length of timeseries
        assert num_ts > 2
        with self.assertRaises(AssertionError):
            tasks.replace_event_timeseries(event, [event.soc_start, event.soc_end])

        # check None values
        with self.assertRaises(AssertionError):
            tasks.replace_event_timeseries(
                event, [event.soc_start, None] + [event.soc_end] * (num_ts - 2)
            )

        # success
        tasks.replace_event_timeseries(
            event, [event.soc_start] + [0] * (num_ts - 2) + [event.soc_end]
        )
        assert event.timeseries["soc"][1] == 0

    def test_apply_depot_strategy(self):
        # basic test
        django_scenario = self.create_depot_simulation()
        tasks.apply_depot_strategy(django_scenario, "balanced")


class ConsumptionTestCase(TransactionTestCase):
    def test_missing_temperature(self):
        django_scenario, simba_schedule, args = build_scenario()
        missing_temp_text = (
            "uses a consumption LUT for consumption calculation but the scenario "
            "has no Temperature object for temperature lookup. Default value for "
            "temperature of 20 °C is used."
        )
        with self.assertLogs(logger="custom") as cm:
            schedule, args = tasks.get_schedule_from_db(django_scenario)
            # Check if any log entry contains the substring
            self.assertTrue(
                any(missing_temp_text in message for message in cm.output),
                "Expected log message not found in output",
            )
        # Add a temperature object
        temp = create_temperatures(django_scenario)
        with self.assertLogs(logger="custom") as cm:
            schedule, args = tasks.get_schedule_from_db(django_scenario)
            # Check if any log entry contains the substring
            self.assertFalse(
                any(missing_temp_text in message for message in cm.output),
                "Expected log message not found in output",
            )
        temp.id += 1
        temp.save()
        # Two temperatures for the same scenario should raise an exception
        self.assertRaises(Exception, tasks.get_schedule_from_db, django_scenario=django_scenario)

    def test_sim_with_consumption(self):
        django_scenario, simba_schedule, args = build_scenario()
        vehicle_types = VehicleType.objects.filter(scenario=django_scenario)
        cons = Consumption.objects.filter(scenario=django_scenario)

        # consumption.scenario and vehicle_classes are not nullable
        def check_nullable_scenario(consumption):
            consumption.scenario = None
            consumption.save()

        def check_nullable_vehicle_class(consumption):
            consumption.vehicle_class = None
            consumption.save()

        with self.assertRaises(Exception):
            check_nullable_scenario(cons.first())

        with self.assertRaises(Exception):
            check_nullable_vehicle_class(cons.first())

        for vt in vehicle_types:
            vt.consumption = None
            vt.vehicle_classes.set([])
            vt.save()

        # Should fail because neither vehicle_type has consumption nor a consumption points towards
        # the vehicle via vehicle_class
        self.assertRaises(Exception, run_simba_scenario, django_scenario=django_scenario)

        for vt in vehicle_types:
            vt.consumption = 1
            vt.save()

        # Does not fail with consumption
        run_simba_scenario(django_scenario=django_scenario)
        sum_consumption = 0
        for event in Event.objects.filter(scenario=django_scenario, event_type=EventType.DRIVING):
            sum_consumption += event.soc_end - event.soc_start

        for vt in vehicle_types:
            vt.consumption *= 2
            vt.save()

        # Does not fail with consumption
        run_simba_scenario(django_scenario=django_scenario)
        sum_consumption_double = 0
        for event in Event.objects.filter(scenario=django_scenario, event_type=EventType.DRIVING):
            sum_consumption_double += event.soc_end - event.soc_start

        self.assertAlmostEqual(sum_consumption * 2, sum_consumption_double)

        for vt in vehicle_types:
            vt.vehicle_classes.add(VehicleClass.objects.first())

        # Should fail because vehicle_type has consumption but also consumption objects point
        # towards the same vehicle type
        self.assertRaises(Exception, run_simba_scenario, django_scenario=django_scenario)

        for vt in vehicle_types:
            vt.consumption = None
            vt.save()

        # Does not fail with consumption table
        run_simba_scenario(django_scenario=django_scenario)
        sum_consumption = 0
        for event in Event.objects.filter(scenario=django_scenario, event_type=EventType.DRIVING):
            sum_consumption += event.soc_end - event.soc_start

        assert Consumption.objects.all().count() == 1
        consumption = Consumption.objects.first()
        consumption.values = [v * 2 for v in consumption.values]
        consumption.save()

        run_simba_scenario(django_scenario=django_scenario)
        sum_consumption_double = 0
        for event in Event.objects.filter(scenario=django_scenario, event_type=EventType.DRIVING):
            sum_consumption_double += event.soc_end - event.soc_start

    def test_consumption_from_df(self):
        django_scenario, simba_schedule, args = build_scenario()
        # incline = HeightDifference/Distance
        # level_of_loading = Load/MaxLoad of the VehicleType
        # Speed in km/h
        # t_amb ambient temperature in °C
        # consumption in kWh/km
        # eg combinations of no incline, incline, empty bus, full bus, slow bus and fast bus,
        # low temp and high temp
        data_points = [
            [0, 0, 5, -6, 10],
            [0.1, 0, 5, -6, 20],
            [0, 1, 5, -6, 15],
            [0.1, 1, 5, -6, 30],
            [0, 0, 50, -6, 1],
            [0.1, 0, 50, -6, 2],
            [0, 1, 50, -6, 1.5],
            [0.1, 1, 50, -6, 3],
            [0, 0, 5, 28, 5],
            [0.1, 0, 5, 28, 10],
            [0, 1, 5, 28, 7.5],
            [0.1, 1, 5, 28, 15],
            [0, 0, 50, 28, 0.5],
            [0.1, 0, 50, 28, 1],
            [0, 1, 50, 28, 0.75],
            [0.1, 1, 50, 28, 1.5],
            # eg 10% incline, 100% full bus, 50 km/h, +28°C leads to consumption of 1.5 kWh/km
        ]
        df = pd.DataFrame(
            columns=[
                "incline",
                "level_of_loading",
                "mean_speed_kmh",
                "t_amb",
                "consumption_kwh_per_km",
            ],
            data=data_points,
        )
        run_simba_scenario(django_scenario=django_scenario)
        sum_consumption = 0
        for event in Event.objects.filter(scenario=django_scenario, event_type=EventType.DRIVING):
            sum_consumption += event.soc_end - event.soc_start

        consumption = Consumption.from_df(df)
        vc = VehicleClass.objects.first()
        Consumption.objects.filter(vehicle_class=vc).delete()
        consumption.vehicle_class = vc

        consumption.scenario = django_scenario
        consumption.save()
        run_simba_scenario(django_scenario=django_scenario)
        sum_consumption_new = 0
        for event in Event.objects.filter(scenario=django_scenario, event_type=EventType.DRIVING):
            sum_consumption_new += event.soc_end - event.soc_start
        assert sum_consumption_new != sum_consumption

    def test_get_consumption(self):
        scenario = Scenario.objects.create(name="my scenario")
        vehicle_class = VehicleClass.objects.create(name="my vehicle class", scenario=scenario)
        consumption_instance = Consumption.objects.create(
            name="My Consumption",
            columns=["speed", "other"],
            data_points=[[10, 1], [100, 3]],
            values=[1, 2],
            vehicle_class=vehicle_class,
            scenario=scenario,
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
            vehicle_class=vehicle_class,
            scenario=scenario,
        )
        c2 = Consumption.objects.create(
            name="My Other Consumption 2",
            columns=[
                "speed",
            ],
            data_points=[[1], [10], [50], [100]],
            values=[1, 2, 3, 50],
            vehicle_class=vehicle_class,
            scenario=scenario,
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
            vehicle_class=vehicle_class,
            scenario=scenario,
        )
        self.assertAlmostEqual(c.get_consumption((10, 20, 1)), 1)
        self.assertAlmostEqual(c.get_consumption((5, 20, 1)), 3)
        self.assertAlmostEqual(c.get_consumption((10, 20, 2)), 1.5)
        self.assertAlmostEqual(c.get_consumption((10, 25, 3)), 3)
        delta = 1e-9
        self.assertAlmostEqual(c.get_consumption((0 + delta, 30 - delta, 3 - delta)), 8)
        self.assertNotEqual(c.get_consumption((0 + delta, 30 - delta, 3 - delta)), 8)


class TripTestCase(TestCase):
    def test_level_of_loading(self):
        django_scenario, simba_schedule, args = build_scenario()
        def_allowed_mass = 200
        def_empty_mass = 100
        for vt in VehicleType.objects.filter(scenario=django_scenario):
            vt.allowed_mass = 200
            vt.empty_mass = 100
            vt.save()

        rotation = Rotation.objects.filter(scenario=django_scenario).first()
        trip = Trip.objects.filter(rotation=rotation).order_by("arrival_time").first()
        trip.loaded_mass = 0
        trip.save()
        schedule, args = tasks.get_schedule_from_db(django_scenario)
        schedule_trip = schedule.rotations[rotation.id].trips[0]

        # make sure the right trip is identified
        assert schedule_trip.arrival_time == trip.arrival_time
        assert schedule_trip.departure_time == trip.departure_time

        assert schedule_trip.level_of_loading == 0

        trip.loaded_mass = 50
        trip.save()
        schedule, args = tasks.get_schedule_from_db(django_scenario)
        schedule_trip = schedule.rotations[rotation.id].trips[0]
        assert schedule_trip.level_of_loading == trip.loaded_mass / (
            def_allowed_mass - def_empty_mass
        )

        trip.loaded_mass = 200
        trip.save()
        # make sure warning is given when overloading
        with self.assertLogs(logger="custom") as cm:
            schedule, args = tasks.get_schedule_from_db(django_scenario)
            # Check if any log entry contains the substring
            self.assertTrue(
                any("Level of loading is out of [0,1] range" in message for message in cm.output),
                "Expected log message not found in output",
            )

        # make sure warning is given when underloading
        trip.loaded_mass = -1
        trip.save()
        with self.assertLogs(logger="custom") as cm:
            schedule, args = tasks.get_schedule_from_db(django_scenario)
            # Check if any log entry contains the substring
            self.assertTrue(
                any("Level of loading is out of [0,1] range" in message for message in cm.output),
                "Expected log message not found in output",
            )

        trip.loaded_mass = None
        trip.save()
        with self.assertLogs(logger="custom") as cm:
            schedule, args = tasks.get_schedule_from_db(django_scenario)
            # Check if any log entry contains the substring
            self.assertTrue(
                any(
                    "has no loaded mass but the vehicle_type which services this" in message
                    for message in cm.output
                ),
                "Expected log message not found in output",
            )

        for allowed_mass, empty_mass in [[None, None], [None, 100], [100, None]]:
            for vt in VehicleType.objects.filter(scenario=django_scenario):
                vt.allowed_mass = allowed_mass
                vt.empty_mass = empty_mass
                vt.save()
            with self.assertLogs(logger="custom") as cm:
                schedule, args = tasks.get_schedule_from_db(django_scenario)
                # Check if any log entry contains the substring
                self.assertTrue(
                    any(
                        "is serviced by a vehicle_type with a consumption lut. "
                        "The vehicle_type does not contain the allowed and empty mass." in message
                        for message in cm.output
                    ),
                    "Expected log message not found in output",
                )


class AllowOppChargingTestCase(TestCase):
    def test_allow_opp_charging(self):
        django_scenario, simba_schedule, args = build_scenario()
        for station in Station.objects.filter(scenario=django_scenario):
            if station.charge_type == EnumChargeType.DEPOT:
                continue
            station.is_electrified = True
            station.total_power = 10_000
            station.power_per_charger = 500
            station.charge_type = EnumChargeType.OPPORTUNITY
            station.voltage_level = EnumVoltageLevel.VOLTAGE_HV
            station.save()

        for rot in Rotation.objects.filter(scenario=django_scenario):
            rot.allow_opportunity_charging = False
            rot.save()

        for vt in VehicleType.objects.filter(scenario=django_scenario):
            vt.opportunity_charging_capable = True
            vt.save()

        tasks.is_consistent(django_scenario)
        sched, scen = run_simba_scenario(django_scenario)
        assert (
            Event.objects.filter(
                scenario=django_scenario, event_type=EventType.CHARGING_OPPORTUNITY
            ).count()
            == 0
        )

        for vt in VehicleType.objects.filter(scenario=django_scenario):
            vt.opportunity_charging_capable = False
            vt.save()

        run_simba_scenario(django_scenario)
        assert (
            Event.objects.filter(
                scenario=django_scenario, event_type=EventType.CHARGING_OPPORTUNITY
            ).count()
            == 0
        )

        for rot in Rotation.objects.filter(scenario=django_scenario):
            rot.allow_opportunity_charging = True
            rot.save()

        run_simba_scenario(django_scenario)

        assert (
            Event.objects.filter(
                scenario=django_scenario, event_type=EventType.CHARGING_OPPORTUNITY
            ).count()
            == 0
        )

        # Create case with both vehicle type being capable of opp charging and rotation allowing
        # opp charging. This should create charging opportunity events
        for vt in VehicleType.objects.filter(scenario=django_scenario):
            vt.opportunity_charging_capable = True
            vt.save()

        run_simba_scenario(django_scenario)
        assert (
            Event.objects.filter(
                scenario=django_scenario, event_type=EventType.CHARGING_OPPORTUNITY
            ).count()
            > 0
        )


def create_temperatures(scenario):
    date1 = make_aware(datetime(year=2024, month=1, day=1))
    dt = timedelta(hours=5)
    date2 = date1 + dt
    date3 = date1 - timedelta(days=5)
    temp1 = 25
    temp2 = 0
    temp3 = 100
    t_instance = Temperatures(
        scenario=scenario,
        name="First Temperatures",
        use_only_time=False,
        datetimes=[date1, date2, date3],
        data=[temp1, temp2, temp3],
    )
    t_instance.save()
    return t_instance


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


class SerializerTest(TransactionTestCase):
    def test_serializer(self):
        count_before = count_all_rows()
        django_scenario, _, _ = build_scenario()
        django_scenario.task_id = get_unique_task_id()
        django_scenario.save()
        tasks.run_toolchain_from_scenario(django_scenario, assign_vehicles=True)
        count_after = count_all_rows()
        count_delta = count_after - count_before
        print(f"Simulation Scenario added {count_delta} objects")

        # Load the scenario in the exporter
        exporter = ScenarioJSONImporterExporter()
        visit_all_scenario_queries(exporter, django_scenario)

        # Create json_data. This can be dumped and exported
        json_data = exporter.renderJSON()
        django_scenario.delete()
        # Make sure the db has the same status as before
        # exporter can load the json_data. Passing an InMemoryUploadedFile is also possible
        exporter.loads(json_bytes=json_data)

        # Objects data is flushed when loading json data
        assert exporter.object_data == dict()

        # Object Instances get recreated
        exporter.generate_instances()
        exporter.adjust_foreign_keys()
        exporter.bulk_create()
        exporter.create_many_to_many()
        # The exporter bulk creates objects.
        # To reset the postgres auto increment counter this command is called.
        core.deepcopy.reset_postgres_auto_increments([Scenario._meta.app_label])
        # Importing the scenario creates the same number of objects/rows as the exported scenario.
        assert count_delta == count_all_rows() - count_before


def count_all_rows() -> int:
    """Iterate over all ebustoolbox models and sum up the rows"""
    ebus_models = django.apps.apps.get_app_config("ebustoolbox").get_models()
    count = 0
    for model in ebus_models:
        current = model.objects.count()
        count += current
    return count


class ScheduleReaderTest(TestCase):
    @override_settings(DEBUG="True")
    @override_settings(LOG_LEVEL="DEBUG")
    def test_encodings(self):
        root = "ebustoolbox/static/ebustoolbox/examples/"
        files = [
            "trips_example_ansi.csv",
            "trips_example_utf-16-be-bom.csv",
            "trips_example_utf-8-bom.csv",
            "trips_example_utf-8.csv",
        ]
        count_trips = None
        for path in files:
            scenario = Scenario.objects.create(name="Test", task_id=get_unique_task_id())
            sr = SimbaScheduleReader(file_path=root + path)
            sr.write_to_db(scenario_id=scenario.id)
            if count_trips is None:
                count_trips = Trip.objects.filter(scenario=scenario).count()
            else:
                new_count = Trip.objects.filter(scenario=scenario).count()
                assert count_trips == new_count, f"{count_trips}/ {new_count}"
            scenario.delete()


class ZipValidationTest(SimpleTestCase):
    @override_settings(DEBUG="True")
    @override_settings(LOG_LEVEL="DEBUG")
    def test_zip_validation(self):
        # Test Exception raising for malformed zips
        # small dummy payload (~200 KB)
        # to large uncompressed
        payload_size = 100
        allowed_size = payload_size - 1
        payload = b"A" * payload_size
        prev = io.BytesIO()
        with zipfile.ZipFile(prev, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("payload.txt", payload)
            with self.assertRaises(ZipFileException):
                validate_zip(z, 99, allowed_size, 99)

        # To many files
        payload = b"A"
        prev = io.BytesIO()
        allowed_file_nr = 1
        with zipfile.ZipFile(prev, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for i in range(allowed_file_nr + 1):
                z.writestr(f"payload{i}.txt", payload)
            with self.assertRaises(ZipFileException):
                validate_zip(z, allowed_file_nr, 100, 99)

        # To deeply nested files
        payload = b"A"
        allowed_nesting = 2
        # recursively wrap previous zip
        for i in range(allowed_nesting + 1):
            new_buf = io.BytesIO()
            with zipfile.ZipFile(new_buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr(f"layer_{i-1}.zip", prev.getvalue())
            prev = new_buf

        with zipfile.ZipFile(prev, "r", compression=zipfile.ZIP_DEFLATED) as z:
            with self.assertRaises(ZipFileException):
                validate_zip(z, 99, 100, allowed_nesting)

        # test positive validation
        payload_size = 20
        payload = b"A" * payload_size
        allowed_file_nr = 3
        allowed_size = allowed_file_nr * payload_size
        allowed_nesting = 2
        prev = io.BytesIO()

        with zipfile.ZipFile(prev, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for i in range(allowed_file_nr):
                z.writestr(f"payload{i}.txt", payload)
        for i in range(allowed_nesting):
            new_buf = io.BytesIO()
            with zipfile.ZipFile(new_buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr(f"layer_{i-1}.zip", prev.getvalue())
            prev = new_buf

        with zipfile.ZipFile(prev, "r", compression=zipfile.ZIP_DEFLATED) as z:
            validate_zip(z, allowed_file_nr, allowed_size, allowed_nesting)
            with self.assertRaises(ZipFileException):
                validate_zip(z, allowed_file_nr - 1, allowed_size, allowed_nesting)
            with self.assertRaises(ZipFileException):
                validate_zip(z, allowed_file_nr, allowed_size - 1, allowed_nesting)
            with self.assertRaises(ZipFileException):
                validate_zip(z, allowed_file_nr, allowed_size, allowed_nesting - 1)
