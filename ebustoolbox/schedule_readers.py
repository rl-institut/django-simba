import csv
from datetime import datetime
from pathlib import Path
from typing import Callable
from abc import ABC, abstractmethod

from django import forms
from django.utils.timezone import make_aware
from inspect import signature

from core.models import Progress
from ebustoolbox.models import (
    Scenario,
    Station,
    VehicleType,
    Rotation,
    Line,
    Route,
    Trip,
    EnumChargeType,
)


def get_options_form(reader_num: int):
    match reader_num:
        case _:
            return SimbaScheduleReader.get_options_form()


def function_signature_to_form(function: Callable):
    sig = signature(function)

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
        print(argument, field, argument_type)
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
    raise NotImplementedError(f"Schedule Reader with {reader_num} not found")


class SimbaScheduleReader(ScheduleReader):
    def __init__(
        self,
        file_path: str,
        default_charging_type: str = "oppb",
    ):
        self.file_path: Path = Path(file_path)
        self.default_capacity = 99.99
        if default_charging_type not in [EnumChargeType.DEPOT, EnumChargeType.OPPORTUNITY]:
            raise Exception("""Default charging type has to be of type "depb" or "oppb" """)

        self.default_charging_type = default_charging_type
        self.encoding = "utf-8"
        self.errors = []
        self.progress: Progress = None
        # self.file_path: Path = None

        self.DEPARTURE_NAME = "departure_name"
        self.DEPARTURE_TIME = "departure_time"
        self.ARRIVAL_TIME = "arrival_time"
        self.ARRIVAL_NAME = "arrival_name"
        self.DISTANCE = "distance"
        self.VEHICLE_TYPE = "vehicle_type"
        self.CHARGING_TYPE = "charging_type"
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
        """This is help text
        :param: scenario_id: this is the id of the scenario
        """
        self.set_total_work(5)
        self.set_progress(0, "Reading File")
        trip_data = self.file_data_to_dict()

        self.set_progress(1, "Finding Stations")
        # Create Stations
        scenario = Scenario.objects.get(id=scenario_id)
        stations, station_dict = self.get_stations(scenario, trip_data)
        Station.objects.bulk_create(stations)

        self.set_progress(2, "Finding Vehicle Types")
        # Create empty vehicle_types
        vt_dict, vts = self.get_vehicles(scenario, trip_data)
        VehicleType.objects.bulk_create(vts)

        self.set_progress(3, "Finding Rotations")
        # Create Rotations
        rotations, rotations_dict = self.get_rotations(scenario, trip_data, vt_dict)
        Rotation.objects.bulk_create(rotations)

        self.set_progress(4, "Finding Trips")

        # Create Trips and Routes
        lines, routes, trips = self.get_lines_routes_trips(
            rotations_dict, scenario, station_dict, trip_data
        )

        Line.objects.bulk_create(lines)
        Route.objects.bulk_create(routes)
        Trip.objects.bulk_create(trips)
        self.set_progress(5, "Finished")
        return True

    def get_lines_routes_trips(self, rotations_dict, scenario, station_dict, trip_data):
        trips = []
        lines = []
        line_dict = dict()
        routes = list()
        route_id = 1 if Route.objects.last() is None else Route.objects.last().id + 1
        trip_id = 1 if Trip.objects.last() is None else Trip.objects.last().id + 1
        line_id = 1 if Line.objects.last() is None else Line.objects.last().id + 1
        for rotation_id, rotation_trips in trip_data.items():
            for trip in rotation_trips:
                if trip[self.LINE] not in line_dict:
                    line = Line(scenario=scenario, name=trip[self.LINE], id=line_id)
                    line_id += 1
                    lines.append(line)
                    line_dict[trip[self.LINE]] = line
                line = line_dict[trip[self.LINE]]

                route = Route(
                    name=trip[self.DEPARTURE_NAME] + " - " + trip[self.ARRIVAL_NAME],
                    scenario=scenario,
                    departure_station=station_dict[trip[self.DEPARTURE_NAME]],
                    arrival_station=station_dict[trip[self.ARRIVAL_NAME]],
                    distance=trip[self.DISTANCE],
                    line=line,
                )

                t = Trip(
                    rotation=rotations_dict[rotation_id],
                    route=route,
                    scenario=scenario,
                    departure_time=make_aware(trip[self.DEPARTURE_TIME]),
                    arrival_time=make_aware(trip[self.ARRIVAL_TIME]),
                    # ToDo How do we implement getting loaded masses? Ignore?
                    loaded_mass=0,
                )

                t.pk = trip_id
                route.pk = route_id

                trip_id += 1
                route_id += 1

                routes.append(route)
                trips.append(t)
        return lines, routes, trips

    def get_rotations(self, scenario, trip_data, vt_dict):
        rotations = list()
        rotations_dict = dict()
        last_id = 1 if Rotation.objects.last() is None else Rotation.objects.last().id + 1
        i = -1

        for rotation_id, trips in trip_data.items():
            i += 1
            assert (
                len({t[self.VEHICLE_TYPE] for t in trips}) == 1
            ), f"Rotation {rotation_id} contains multiple vehicle types"
            assert (
                len({t[self.CHARGING_TYPE] for t in trip_data[rotation_id]}) == 1
            ), f"Rotation {rotation_id} contains multiple charging types"
            first_trip = trips[0]
            vt = vt_dict[first_trip[self.VEHICLE_TYPE]]
            match str(first_trip[self.CHARGING_TYPE]).lower():
                case EnumChargeType.OPPORTUNITY.value:
                    rot = Rotation(
                        scenario=scenario,
                        name=rotation_id,
                        pk=last_id + i,
                        allow_opportunity_charging=True,
                        vehicle_type=vt[0],
                    )
                case EnumChargeType.DEPOT.value:
                    rot = Rotation(
                        scenario=scenario,
                        name=rotation_id,
                        pk=last_id + i,
                        allow_opportunity_charging=False,
                        vehicle_type=vt[1],
                    )
                case _:
                    raise NotImplementedError
            rotations.append(rot)
            rotations_dict[rotation_id] = rot
        return rotations, rotations_dict

    def get_vehicles(self, scenario, trip_data):
        vts = list()
        vt_dict = dict()
        unique_vts = {trips[0][self.VEHICLE_TYPE] for trips in trip_data.values()}
        last_id = 1 if VehicleType.objects.last() is None else VehicleType.objects.last().id + 1
        for i, name in enumerate(unique_vts):
            default_params = {
                "scenario": scenario,
                "name": name,
                "battery_capacity": self.default_capacity,
                "charging_curve": [[0, self.default_capacity], [1, self.default_capacity]],
            }
            vt_opp = VehicleType(
                **default_params, id=last_id + (i * 2) + 0, opportunity_charging_capable=True
            )
            vt_dep = VehicleType(
                **default_params, id=last_id + (i * 2) + 1, opportunity_charging_capable=False
            )
            vts.extend([vt_opp, vt_dep])
            vt_dict[name] = (vt_opp, vt_dep)
        return vt_dict, vts

    def get_stations(self, scenario, trip_data):
        stations = list()
        station_dict = dict()

        # make sure the trips are sorted
        for rot_id, trips in trip_data.items():
            trip_data[rot_id] = sorted(trips, key=lambda x: x[self.ARRIVAL_TIME])

        # Assume first and last stop are always depots
        # Get the arrival name of the first trip and departure_name of the last trip.
        depot_stations = {
            trips[num][name]
            for trips in trip_data.values()
            for num, name in [(1, self.ARRIVAL_NAME), (-1, self.DEPARTURE_NAME)]
        }

        unique_arrival_stations = {
            trip[self.ARRIVAL_NAME] for trips in trip_data.values() for trip in trips
        }
        unique_departure_stations = {
            trip[self.DEPARTURE_NAME] for trips in trip_data.values() for trip in trips
        }
        unique_stations = unique_arrival_stations.union(unique_departure_stations)
        last_id = 1 if Station.objects.last() is None else Station.objects.last().id + 1
        for i, name in enumerate(unique_stations):
            station = Station(scenario=scenario, name=name, id=last_id + i)
            if name in depot_stations:
                station.is_electrified = True
                station.charge_type = EnumChargeType.DEPOT.value
            stations.append(station)
            station_dict[name] = station

        return stations, station_dict

    def file_data_to_dict(self) -> dict[str, []]:
        trip_data = dict()

        # Possible error texts
        duration_error = "has no duration. Remove it from the schedule"

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
                self.CHARGING_TYPE,
                self.LINE,
            ]:
                if column not in trip.keys():
                    missing_column = True
                    self.errors.append(f"Column {column} is missing.")
            if missing_column:
                newline = "\n"
                raise Exception(
                    f"At least on column is missing from the file {self.file_path.stem}. "
                    f"{newline.join(self.errors)}"
                )

            for i, trip in enumerate(trip_reader):
                rotation_id = trip[self.ROTATION_ID]
                if rotation_id not in trip_data:
                    trip_data[rotation_id] = []
                trip_d = dict()
                trip_d[self.DEPARTURE_NAME] = trip[self.DEPARTURE_NAME]
                trip_d[self.DEPARTURE_TIME] = datetime.fromisoformat(trip[self.DEPARTURE_TIME])
                trip_d[self.ARRIVAL_TIME] = datetime.fromisoformat(trip[self.ARRIVAL_TIME])
                trip_d[self.ARRIVAL_NAME] = trip[self.ARRIVAL_NAME]
                trip_d[self.DISTANCE] = float(trip[self.DISTANCE])
                trip_d[self.VEHICLE_TYPE] = trip[self.VEHICLE_TYPE]
                if trip[self.CHARGING_TYPE] != "":
                    trip_d[self.CHARGING_TYPE] = trip[self.CHARGING_TYPE]
                else:
                    trip_d[self.CHARGING_TYPE] = self.default_charging_type
                trip_d[self.LINE] = trip[self.LINE]

                assert (
                    trip_d[self.DEPARTURE_TIME] < trip_d[self.ARRIVAL_TIME]
                ), f"Line {i+1}: Trip {trip_d} {duration_error}"

                trip_data[rotation_id].append(trip_d)
        return trip_data
