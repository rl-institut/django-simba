from abc import ABC, abstractmethod
import csv
from datetime import datetime, timedelta, timezone as tz
from enum import Enum
import logging
import inspect
from pathlib import Path
from random import random

import requests

from django.contrib.gis.geos import Point
from django.db.models import QuerySet, Min, Max
from tqdm.auto import tqdm
from typing import Callable, Type, Iterable
from uuid import UUID

from django import forms
from django.utils import timezone
from django.contrib.gis.db import models

import eflips
from eflips.ingest import DummyIngester, AbstractIngester
from eflips.ingest.dummy import BusType
from eflips.ingest.vdv import VdvIngester
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.models import Progress
from ebusdjango import settings
from ebustoolbox import util
from ebustoolbox.models import (
    Scenario,
    Station,
    VehicleType,
    Rotation,
    Line,
    Route,
    Trip,
    EnumChargeType,
    EnumVoltageLevel,
    X,
    Y,
)

logger = logging.getLogger("custom")


def get_options_form(reader_num: int):
    match reader_num:
        case 1:
            return SimbaScheduleReader.get_options_form()
        case 2:
            return EflipsIngestScheduleReaderDummy.get_options_form(DummyIngester)
        case 3:
            return EflipsIngestScheduleReaderVDV.get_options_form(VdvIngester)
    raise NotImplementedError


def function_signature_to_form(function: Callable):
    sig = inspect.signature(function)

    def ScheduleReaderOptionsFormFactory(classname, fields: dict):
        return type(
            f"{classname}",
            (forms.Form,),
            fields,
        )

    fields = dict()

    parameters = {name: argument for name, argument in sig.parameters.items()}
    del parameters["self"]
    for name, argument in parameters.items():
        argument_type = argument.annotation.__qualname__
        match argument_type:
            case str.__qualname__:
                if name.find("file") > -1:
                    field = forms.FileField(required=True)
                else:
                    field = forms.CharField(max_length=100, required=True)
            case int.__qualname__:
                field = forms.IntegerField(required=True)
            case float.__qualname__:
                field = forms.DecimalField(max_digits=10, decimal_places=2, initial=1e5)
            case bool.__qualname__:
                field = forms.BooleanField()
            case datetime.__qualname__:
                field = forms.DateTimeField(widget=DateTimeInput)
            case _:
                raise NotImplementedError
        fields[name] = field
    form = ScheduleReaderOptionsFormFactory("ScheduleReaderOptionsForm", fields)
    return form


class DateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"


class ScheduleReader(ABC):
    @abstractmethod
    def write_to_db(self, scenario_id) -> bool:
        pass

    @abstractmethod
    def get_errors(self) -> [str]:
        pass

    @abstractmethod
    def set_observer(self, progress: Progress) -> None:
        pass

    @classmethod
    def get_options_form(cls):
        return function_signature_to_form(cls.__init__)


def get_schedule_reader_factory(reader_num: int) -> type(ScheduleReader):
    """Returns Schedule Reader to handle the schedule csv."""
    match reader_num:
        case 1:
            return SimbaScheduleReader
        case 2:
            return EflipsIngestScheduleReaderDummy
        case 3:
            return EflipsIngestScheduleReaderVDV

    raise NotImplementedError(f"Schedule Reader with {reader_num} not found")


def place_not_found_stations(scenario):
    stations_without_geo = Station.objects.filter(scenario=scenario, geom__isnull=True)
    if Station.objects.filter(scenario=scenario, geom__isnull=False).count() > 1:
        max_x = (
            Station.objects.filter(scenario=scenario).aggregate(
                max_x=Max(X("geom", output_field=models.DecimalField()))
            )
        )["max_x"]
        min_x = (
            Station.objects.filter(scenario=scenario).aggregate(
                min_x=Min(X("geom", output_field=models.DecimalField()))
            )
        )["min_x"]
        max_y = (
            Station.objects.filter(scenario=scenario).aggregate(
                max_y=Max(Y("geom", output_field=models.DecimalField()))
            )
        )["max_y"]
        delta_x = float(min(0.1, max_x - min_x))
        for i, station in enumerate(stations_without_geo):
            x = float(float(min_x) + i * delta_x / (max(1, len(stations_without_geo) - 1)))
            y = float(max_y) + 0.05
            station.geom = Point(x, y, 0)
            station.save()
    else:
        logger.warning(
            "Stations are placed randomly around Berlin, " "since not a single station was located."
        )
        for station in stations_without_geo:
            station.geom = Point(51.5 + random(), 12.5 + random() * 10)
            station.save()


class SimbaScheduleReader(ScheduleReader):
    class SimbaScheduleReaderException(Exception):
        pass

    def __init__(
        self,
        file_path: str,
    ):
        self.errors = []
        self.file_path: Path = Path(file_path)
        self.default_capacity = 99.99
        self.encoding = "utf-8"
        self.progress: Progress = None
        self.vehicles_opportunity_charging_capable = True

        self.DEPARTURE_NAME = "departure_name"
        self.DEPARTURE_TIME = "departure_time"
        self.ARRIVAL_TIME = "arrival_time"
        self.ARRIVAL_NAME = "arrival_name"
        self.DISTANCE = "distance"
        self.VEHICLE_TYPE = "vehicle_type"
        self.LINE = "line"
        self.ROTATION_ID = "rotation_id"

    def get_errors(self) -> [str]:
        return self.errors

    def set_observer(self, progress: Progress) -> None:
        self.progress = progress

    def set_total_work(self, i: int):
        if self.progress:
            self.progress.total_work = i
            self.progress.save()

    def set_progress(self, i: int, status: str = None):
        if self.progress:
            self.progress.current_work = i
            if status is not None:
                self.progress.status = status
            self.progress.save()

    def write_to_db(self, scenario_id: int) -> bool:
        """Write data to the database using the initialized SimbaScheduleReader.
        :param: scenario_id: this is the id of the scenario
        """
        try:
            self.set_total_work(5)
            self.set_progress(0, "Lese Datei")
            trip_data = self.file_data_to_dict()

            self.set_progress(1, "Finde Stationen")
            # Create Stations
            scenario = Scenario.objects.get(id=scenario_id)
            stations, station_dict = self.get_stations(scenario, trip_data)
            Station.objects.bulk_create(stations)
            add_station_locations(Station.objects.filter(scenario=scenario))

            add_elevations(Station.objects.filter(scenario=scenario, geom__isnull=False))
            place_not_found_stations(scenario)

            self.set_progress(2, "Finde Fahrzeugtypen")
            # Create empty vehicle_types
            vt_dict, vts = self.get_vehicles(scenario, trip_data)
            VehicleType.objects.bulk_create(vts)

            self.set_progress(3, "Finde Umläufe")
            # Create Rotations
            rotations, rotations_dict = self.get_rotations(scenario, trip_data, vt_dict)
            Rotation.objects.bulk_create(rotations)

            self.set_progress(4, "Finde Fahrten")

            # Create Trips and Routes
            lines, routes, trips = self.get_lines_routes_trips(
                rotations_dict, scenario, station_dict, trip_data
            )

            if not self.errors:
                Line.objects.bulk_create(lines)
                Route.objects.bulk_create(routes)
                Trip.objects.bulk_create(trips)
            self.set_progress(5, "Fahrten erstellt")
        except self.SimbaScheduleReaderException:
            return False
        return len(self.errors) == 0

    def get_lines_routes_trips(self, rotations_dict, scenario, station_dict, trip_data):
        trips = []
        lines = []
        line_dict = dict()
        routes = list()
        existing_routes = dict()
        trip_overlap_errors = []  # collect trips that overlap
        duration_errors = []  # collect trips that have no or negative duration
        trip_previous_station_errors = {}  # collect trips that don't end at their previous depot
        route_id = util.get_next_id(Route)
        trip_id = util.get_next_id(Trip)
        line_id = util.get_next_id(Line)
        for rotation_id, rotation_trips in tqdm(trip_data.items()):
            sorted_trips = sorted(rotation_trips, key=lambda trip: trip["departure_time"])
            prev_arrival_time = sorted_trips[0]["departure_time"] - timedelta(hours=1)
            prev_arrival_name = sorted_trips[0]["departure_name"]
            for trip in sorted_trips:
                # copy/update previous arrival name and time in case of error
                saved_arrival_time = prev_arrival_time
                saved_arrival_name = prev_arrival_name
                prev_arrival_time = trip["arrival_time"]
                prev_arrival_name = trip["arrival_name"]

                if not trip[self.DEPARTURE_TIME] < trip[self.ARRIVAL_TIME]:
                    # trip arrives before it departs
                    duration_errors.append(trip["row"])
                if trip["departure_time"] < saved_arrival_time:
                    # trip overlaps with another (departs before previous arrival)
                    trip_overlap_errors.append(trip["row"])
                    continue
                if trip["departure_name"] != saved_arrival_name:
                    # trip does not end where it started from
                    # aggregate by expected station
                    try:
                        trip_previous_station_errors[prev_arrival_name].append(trip["row"])
                    except KeyError:
                        trip_previous_station_errors[prev_arrival_name] = [trip["row"]]
                    continue
                if trip[self.LINE] not in line_dict:
                    line = Line(scenario=scenario, name=trip[self.LINE], id=line_id)
                    line_id += 1
                    lines.append(line)
                    line_dict[trip[self.LINE]] = line
                line = line_dict[trip[self.LINE]]

                route = existing_routes.get(
                    (
                        station_dict[trip[self.DEPARTURE_NAME]].id,
                        station_dict[trip[self.ARRIVAL_NAME]].id,
                        trip[self.DISTANCE],
                        line,
                    )
                )
                if not route:
                    route = Route(
                        name=trip[self.DEPARTURE_NAME] + " - " + trip[self.ARRIVAL_NAME],
                        scenario=scenario,
                        departure_station=station_dict[trip[self.DEPARTURE_NAME]],
                        arrival_station=station_dict[trip[self.ARRIVAL_NAME]],
                        distance=trip[self.DISTANCE],
                        line=line,
                    )
                    existing_routes[
                        (
                            station_dict[trip[self.DEPARTURE_NAME]].id,
                            station_dict[trip[self.ARRIVAL_NAME]].id,
                            trip[self.DISTANCE],
                            line,
                        )
                    ] = route
                    route.pk = route_id
                    route_id += 1
                    routes.append(route)

                # handle timezone-related issues: force aware in UTC. Mainly for display reasons
                departure_time = trip[self.DEPARTURE_TIME]
                if timezone.is_naive(departure_time):
                    departure_time = timezone.make_aware(departure_time, timezone=tz.utc)
                arrival_time = trip[self.ARRIVAL_TIME]
                if timezone.is_naive(arrival_time):
                    arrival_time = timezone.make_aware(arrival_time, timezone=tz.utc)

                t = Trip(
                    rotation=rotations_dict[rotation_id],
                    route=route,
                    scenario=scenario,
                    departure_time=departure_time,
                    arrival_time=arrival_time,
                    # ToDo How do we implement getting loaded masses? Ignore?
                    loaded_mass=0,
                )

                t.pk = trip_id
                trip_id += 1
                trips.append(t)

        # handle collected errors
        if duration_errors:
            self.errors.append(
                f"Fahrt(en) in Zeile {', '.join(map(str, duration_errors))} haben keine oder eine "
                "negative Fahrtdauer. Bitte ergänzen Sie Fahrzeiten oder entfernen Sie die Fahrten."
            )

        if trip_overlap_errors:
            self.errors.append(
                f"Fahrt in Zeile {', '.join(map(str, trip_overlap_errors))} überschneidet sich "
                f"(startet vor der vorherigen Ankunft)"
            )
        for station, trip_lines in trip_previous_station_errors.items():
            self.errors.append(
                f"Fahrt in Zeile {', '.join(map(str, trip_lines))} "
                f"startet nicht an der vorherigen Station {station}"
            )
        return lines, routes, trips

    def get_rotations(self, scenario, trip_data, vt_dict):
        rotations = list()
        rotations_dict = dict()
        last_id = util.get_next_id(Rotation)
        i = -1

        for rotation_id, trips in trip_data.items():
            i += 1
            if not (len({t[self.VEHICLE_TYPE] for t in trip_data[rotation_id]}) == 1):
                self.errors.append(f"Umlauf {rotation_id} enthält mehrere Fahrzeugtypen")
            first_trip = trips[0]
            vt = vt_dict[first_trip[self.VEHICLE_TYPE]]
            rot = Rotation(
                scenario=scenario,
                name=rotation_id,
                pk=last_id + i,
                vehicle_type=vt,
            )
            rotations.append(rot)
            rotations_dict[rotation_id] = rot
        return rotations, rotations_dict

    def get_vehicles(self, scenario, trip_data):
        vts = list()
        vt_dict = dict()
        unique_vts = {trips[0][self.VEHICLE_TYPE] for trips in trip_data.values()}
        last_id = util.get_next_id(VehicleType)
        for i, name in enumerate(unique_vts):
            default_params = {
                "scenario": scenario,
                "name": name,
                "name_short": name,
                "battery_capacity": self.default_capacity,
                "charging_curve": [[0, self.default_capacity], [1, self.default_capacity]],
            }
            vt_opp = VehicleType(
                **default_params,
                id=last_id + i,
                opportunity_charging_capable=self.vehicles_opportunity_charging_capable,
            )
            vts.append(vt_opp)
            vt_dict[name] = vt_opp
        return vt_dict, vts

    def get_stations(self, scenario, trip_data):
        stations = list()
        station_dict = dict()

        # make sure the trips are sorted
        for rot_id, trips in trip_data.items():
            trip_data[rot_id] = sorted(trips, key=lambda x: x[self.ARRIVAL_TIME])

        # Assume first and last stop are always depots
        # Get the departure_name of the first trip and arrival_name of the last trip.
        depot_stations = {
            trips[num][name]
            for trips in trip_data.values()
            for num, name in [(-1, self.ARRIVAL_NAME), (0, self.DEPARTURE_NAME)]
        }

        unique_arrival_stations = {
            trip[self.ARRIVAL_NAME] for trips in trip_data.values() for trip in trips
        }
        unique_departure_stations = {
            trip[self.DEPARTURE_NAME] for trips in trip_data.values() for trip in trips
        }
        unique_stations = unique_arrival_stations.union(unique_departure_stations)
        last_id = util.get_next_id(Station)
        for i, name in enumerate(unique_stations):
            station = Station(scenario=scenario, name=name, name_short=name, id=last_id + i)
            if name in depot_stations:
                station.is_electrified = True
                station.charge_type = EnumChargeType.DEPOT.value
                station.voltage_level = EnumVoltageLevel.VOLTAGE_MV.value
            stations.append(station)
            station_dict[name] = station

        return stations, station_dict

    def file_data_to_dict(self) -> dict[str, []]:
        trip_data = dict()

        with open(self.file_path, encoding=self.encoding) as file:
            trip_reader = csv.DictReader(file)
            trip = next(iter(trip_reader))
            missing_column = False
            for column in [
                self.DEPARTURE_TIME,
                self.DEPARTURE_NAME,
                self.ARRIVAL_TIME,
                self.ARRIVAL_NAME,
                self.ROTATION_ID,
                self.VEHICLE_TYPE,
                self.LINE,
            ]:
                if column not in trip.keys():
                    missing_column = True
                    self.errors.append(f"Spalte {column} fehlt")

            if missing_column:
                raise self.SimbaScheduleReaderException
            # Jump to beginning of file for iteration
            file.seek(0)

            # Skip the first line containing headers / column names
            next(trip_reader)
            for i, trip in enumerate(trip_reader):
                rotation_id = trip[self.ROTATION_ID]
                if rotation_id not in trip_data:
                    trip_data[rotation_id] = []
                trip_d = {
                    self.DEPARTURE_NAME: trip[self.DEPARTURE_NAME],
                    self.DEPARTURE_TIME: datetime.fromisoformat(trip[self.DEPARTURE_TIME]),
                    self.ARRIVAL_TIME: datetime.fromisoformat(trip[self.ARRIVAL_TIME]),
                    self.ARRIVAL_NAME: trip[self.ARRIVAL_NAME],
                    self.DISTANCE: float(trip[self.DISTANCE]),
                    self.VEHICLE_TYPE: trip[self.VEHICLE_TYPE],
                    self.LINE: trip[self.LINE],
                    "row": i + 2,  # line numbers start at 1 instead of 0, skip header
                }

                trip_data[rotation_id].append(trip_d)

        return trip_data

    @classmethod
    def get_options_form(cls):
        class ScheduleReaderForm(forms.Form):
            # basics
            file_path = forms.FileField(
                label="Fahrplan Datei (.csv)",
                required=True,
                help_text=".csv Datei mit den Spalten: rotation_id, departure_station, departure_time, "
                "arrival_station, arrival_time, distance, vehicle_type",
            )

        return ScheduleReaderForm


class EflipsIngestScheduleReaderBase(ScheduleReader, ABC):
    """
    This class is the base class for the various eflips-ingest schedule readers. It implements the common
    functionality for all the readers.
    """

    @abstractmethod
    def __init__(self):
        """
        This method initializes the class. In the django-simba world, initial validity checks could be done here.
        However, per Paul's EMail from  2024-03-24, eflips-ingest shims should do the initial validity checks in the
        meth:`write_to_db` method.

        This method needs to be overridden by the subclasses. The arguments during initialization should be the
        parameters that are needed for the meth:`validate` method of the eflips-ingest class that the shim is
        for.
        """
        # As meth:`get_errors` should always return a list, we initialize it here.
        super().__init__()

        self._errors = []
        self._progress: Progress = []

        # Local import to get around circular import
        from ebustoolbox.tasks import create_db_url

        self._database_url = create_db_url()

        # This is useless, but it is here to make the instance variables available in the methods.
        self._ingester: AbstractIngester = None  # Overridden in the subclasses

        # Overridden in the subclasses, this should be the parameters for the meth:`prepare` method of the
        # eflips-ingest class that the shim is for.
        self._kwargs = {
            "progress_callback": None
        }  # Overridden in the subclasses. Keep the None for progress_callback.

    def write_to_db(self, scenario_id: int) -> bool:
        """
        This method calls the meth:`prepare` method of the eflips-ingest class that the shim is for. If the validation
        fails, it fills the self.errors list with the error messages and returns False. If the validation passes, it
        calls the meth:`ingest` method of the eflips-ingest class that the shim is for and returns True.

        About handling the scenario_id: eflips-ingest matches scenario_ids through the task_id field, django-simba
        expects task_id to be None. So what we do is:

        1. Call eflips-ingests meth:`prepare` method. If it succeeds, remember the uuid
        2. Load the scenario from the scenario_id given to us by django-simba and remember the task_id
        3. set the task_id of the scenario to the uuid from step 1
        4. Call eflips-ingests meth:`ingest` method with the uuid (== task_id) and the progress_callback function
           given to us through meth:`set_observer`
        5. Change the task_id back to what we remembered in step 2

        param: scenario_id: The id of the scenario that the data should be ingested into.

        """

        validation_result, uuid_or_errors = self._ingester.prepare(**self._kwargs)
        if not validation_result:
            assert isinstance(uuid_or_errors, dict)
            self._errors = [f"{key}: {value}" for key, value in uuid_or_errors.items()]
            return False
        else:
            assert isinstance(uuid_or_errors, UUID)

        engine = create_engine(self._database_url)
        with Session(engine) as session:
            try:
                scenario = (
                    session.query(eflips.model.Scenario)
                    .filter(eflips.model.Scenario.id == scenario_id)
                    .first()
                )
                django_assigned_task_id = scenario.task_id
                scenario.task_id = uuid_or_errors  # This is the uuid from the prepare method

                # We need to commit the session here, because the ingest method will start a new transaction
                session.commit()

                self._ingester.ingest(uuid_or_errors, self._progress_callback)

                scenario.task_id = django_assigned_task_id
            except Exception as e:
                self._errors = [str(e)]
                session.rollback()
                return False
            finally:
                # In any case, we need to set the task_id back to what it was before
                scenario.task_id = django_assigned_task_id
                session.commit()

        return True

    @staticmethod
    def get_options_form(for_class: Type[AbstractIngester]) -> Callable:
        """
        This method returns a django-simba form that can be used to set the parameters for the meth:`__init__` method.

        It introspects the meth:`prepare` method of the eflips-ingest class that the shim is for and creates a form
        based on the parameters of that method.
        """

        def ScheduleReaderOptionsFormFactory(classname, fields: dict):
            return type(
                f"{classname}",
                (forms.Form,),
                fields,
            )

        fields = dict()

        names = for_class.prepare_param_names()
        descriptions = for_class.prepare_param_description()
        signature = inspect.signature(for_class.prepare)
        assert isinstance(names, dict)
        assert isinstance(descriptions, dict)
        assert isinstance(signature, inspect.Signature)
        params_for_us = set(signature.parameters.keys()) - {"progress_callback", "self"}
        assert set(names.keys()) == set(descriptions.keys()) == params_for_us

        for entry in signature.parameters.values():
            parameter_name = entry.name
            if parameter_name == "progress_callback" or parameter_name == "self":
                continue

            form_name = names[parameter_name]
            form_description = descriptions[parameter_name]

            if entry.annotation == str:
                fields[parameter_name] = forms.CharField(
                    label=form_name, help_text=form_description
                )
            elif entry.annotation == int:
                fields[parameter_name] = forms.IntegerField(
                    label=form_name, help_text=form_description
                )
            elif entry.annotation == float:
                fields[parameter_name] = forms.DecimalField(
                    label=form_name, help_text=form_description
                )
            elif entry.annotation == bool:
                fields[parameter_name] = forms.BooleanField(
                    label=form_name, required=False, help_text=form_description
                )
            elif issubclass(entry.annotation, Enum):
                fields[parameter_name] = forms.ChoiceField(
                    choices=[(key.name, value) for key, value in names[parameter_name].items()]
                )
            elif entry.annotation == Path:
                fields[parameter_name] = forms.FileField(
                    label=form_name, help_text=form_description
                )
            else:
                raise NotImplementedError(f"Parametertyp {entry.annotation} nicht unterstützt.")

        return ScheduleReaderOptionsFormFactory(for_class.__name__ + "OptionsForm", fields)

    def get_errors(self) -> [str]:
        """
        This method returns the errors that were collected during the meth:`write_to_db` method.
        """
        return self._errors

    def set_observer(self, progress: Progress) -> None:
        """
        This method sets the progress observer. The progress observer is a django-simba Progress object that the shim
        can use to report the progress of the ingestion process.
        """
        progress.total_work = 100  # We convert our float progress to an integer between 0 and 100
        progress.current_work = 0
        self._progress = progress
        self._progress.save()

    def _progress_callback(self, increment: float) -> None:
        """
        This method is provided to ingest() as a callback function. When called, it updates the observers with the
        current progress. It is called periodically by the ingest() method.
        """
        self._progress.current_work += int(round(self._progress.total_work * increment))
        self._progress.save()


class EflipsIngestScheduleReaderDummy(EflipsIngestScheduleReaderBase):
    """
    This class is a dummy shim for the eflips-ingest DummyIngester class. It is used for testing the shim system.
    """

    def __init__(
        self,
        random_text_file: str,
        name: str,
        depot_count: int,
        line_count: int,
        rotation_per_line: int,
        opportunity_charging: bool,
        bus_type: str,
    ):
        super().__init__()
        self._ingester = DummyIngester(self._database_url)

        # BusType is an enum, wich we need to recreate here
        bus_type = BusType[bus_type]

        # random_text_file is a Path as string, we need to convert it to a Path
        random_text_file = Path(random_text_file)

        self._kwargs = {
            "random_text_file": random_text_file,
            "name": name,
            "depot_count": depot_count,
            "line_count": line_count,
            "rotation_per_line": rotation_per_line,
            "opportunity_charging": opportunity_charging,
            "bus_type": bus_type,
            "progress_callback": None,
        }


class EflipsIngestScheduleReaderVDV(EflipsIngestScheduleReaderBase):
    """
    This class is a dummy shim for the eflips-ingest DummyIngester class. It is used for testing the shim system.
    """

    def __init__(
        self,
        x10_zip_file: str,
    ):
        super().__init__()
        self._ingester = VdvIngester(self._database_url)

        # random_text_file is a Path as string, we need to convert it to a Path
        x10_zip_file = Path(x10_zip_file)

        self._kwargs = {
            "x10_zip_file": x10_zip_file,
            "progress_callback": None,
        }


def find_station_locations(station_names: Iterable) -> list[tuple]:
    from data_scrapers.tasks import search_stations

    foundStations = search_stations(station_names, use_filter=True)
    result = []
    for name in station_names:
        stations = foundStations.get(name)
        if stations is None:
            result.append((None, None))
            continue
        x_avg = sum([station.geom.x for station in stations]) / len(stations)
        y_avg = sum([station.geom.y for station in stations]) / len(stations)
        result.append((x_avg, y_avg))
    return result


def add_station_locations(query: QuerySet):
    if "data_scrapers" not in settings.INSTALLED_APPS:
        logger.error("Data scraper not available")
        return
    station_names = query.values_list("name", flat=True)
    locations = find_station_locations(station_names)
    stations_with_geom = []
    not_found = []
    for station, (x, y) in zip(query, locations):
        if x is None or y is None:
            not_found.append(station.name)
            continue
        station.geom = Point(x, y, z=0)
        stations_with_geom.append(station)
    query.model.objects.bulk_update(stations_with_geom, ["geom"])


def add_elevations(query: QuerySet):
    """Look up elevation for a given geom of a queryset and add it to the database

    Model needs a field of geom with a Point(x,y,z). Elevation data is searched and added to
    the query
    """
    if query.count() == 0:
        return
    locations = query.values_list("geom", flat=True)
    locations_lat_lon = [f"{loc.y},{loc.x}" for loc in locations]

    url = settings.OPENELEVATION_URL + "/api/v1/lookup/"
    param = {"locations": "|".join(locations_lat_lon)}
    response = requests.get(url, params=param)
    if response.status_code != 200:
        logger.warning(response.status_code)
    data = response.json()
    changed_geom = []
    for i, result in enumerate(data["results"]):
        if result["error"]:
            logger.warning(f"Elevation returned an error: {result}")
        obj = query[i]
        assert obj.geom.x == result["longitude"]
        assert obj.geom.y == result["latitude"]
        obj.geom = Point(obj.geom.x, obj.geom.y, result["elevation"])
        changed_geom.append(obj)
    query.model.objects.bulk_update(changed_geom, ["geom"])
