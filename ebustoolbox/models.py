import shutil
from datetime import timedelta
from pathlib import Path
from django.contrib.gis.db.models.functions import Distance, Length
from django.conf import settings
from django.contrib.gis.db import models
from django.contrib.gis.geos import GEOSGeometry
from django.contrib.postgres.fields import ArrayField
from django.db.models import CheckConstraint
from django.dispatch import receiver

from django.db.models import CheckConstraint, Q, F

MINIMAL_TRIP_DURATION_S = 60  # seconds


class Scenario(models.Model):
    class Meta:
        db_table = 'Scenario'

    name = models.TextField(blank=False)
    name_short = models.TextField(blank=True, null=True)
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True)
    task_id = models.UUIDField(default=None, null=True, blank=True)
    finished = models.DateTimeField(default=None, null=True, blank=True)
    simba_options = models.JSONField(default=dict, null=True)
    eflips_depot_options = models.JSONField(default=dict, null=True)

    @classmethod
    def get_default_pk(cls):
        scenario, created = cls.objects.get_or_create(
            name="default_scenario",
        )
        return scenario.pk


@receiver(models.signals.pre_delete, sender=Scenario)
def auto_delete_results_on_delete(sender, instance, **kwargs):
    """Delete the scenario results folder if the scenario is deleted from the database

    :param sender: Model which sends signal
    :param instance: instance of a model which gets deleted
    :param kwargs: other arguments
    :return:
    """
    if instance.task_id is not None:
        try:
            shutil.rmtree((Path(settings.UPLOAD_PATH) / instance.task_id))
        except FileNotFoundError:
            # The Folder does not exist. That is not a problem
            pass


class UploadedFile(models.Model):
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE)
    file = models.FileField(upload_to=settings.UPLOAD_PATH)


@receiver(models.signals.pre_delete, sender=UploadedFile)
def auto_delete_file_on_delete(sender, instance, **kwargs):
    if instance.file:
        path = Path(instance.file.path)
        if path.exists():
            path.unlink()


class BatteryType(models.Model):
    class Meta:
        db_table = 'BatteryType'

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)

    # relative to gross capacity
    specific_mass_kg_per_kwh = models.FloatField(null=False, blank=True)
    # defined in eFLIPS-LCA
    chemistry = models.JSONField(default=dict)


class VehicleType(models.Model):
    class Meta:
        db_table = 'VehicleType'

    scenario = models.ForeignKey(Scenario, null=True, on_delete=models.CASCADE)
    battery_type = models.ForeignKey(BatteryType, null=True, on_delete=models.CASCADE)

    name = models.CharField(max_length=100, blank=False)
    name_short = models.CharField(max_length=100, blank=False, default=name)
    opportunity_charging_capable = models.BooleanField()
    battery_capacity = models.FloatField()
    battery_reserve_capacity = models.FloatField(default=0)
    charging_efficiency = models.FloatField(default=0.95)
    minimum_charging_power = models.FloatField(default=0)

    # SOC, ChargingPower
    charging_curve = ArrayField(ArrayField(models.FloatField(), size=2))
    v2g_curve = ArrayField(ArrayField(models.FloatField(), size=2), null=True)

    # TODO link to consumption table if no value is given here?
    consumption = models.FloatField(default=None, null=True)
    length_m = models.FloatField(default=None, null=True)
    width_m = models.FloatField(default=None, null=True)
    # Including battery and driver, no passengers
    empty_mass_kg = models.FloatField(default=None, null=True)
    allowed_mass_kg = models.FloatField(default=None, null=True)


class Vehicle(models.Model):
    class Meta:
        db_table = 'Vehicle'

    scenario = models.ForeignKey(Scenario, null=True, on_delete=models.CASCADE)

    name = models.CharField(max_length=100, blank=False)
    name_short = models.CharField(blank=True)
    vehicle_type = models.ForeignKey(VehicleType, on_delete=models.CASCADE, null=True, blank=True)

    def save(self, *args, **kwargs):
        # Override save to make certain name_short exists
        if not self.name_short:
            self.name_short = self.name
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


# ToDo Deprecated
class VehicleProperties(models.Model):
    scenario = models.ForeignKey(Scenario, null=True, on_delete=models.CASCADE)

    date = models.DateTimeField()
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE)
    soc = models.FloatField(null=True)


class Rotation(models.Model):
    class Meta:
        db_table = 'Rotation'

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)

    name = models.TextField(blank=False, null=True)
    vehicle_type = models.ForeignKey(VehicleType, null=False, blank=True, on_delete=models.CASCADE)

    # SimBA specific data to make SimBA simulations reproducible
    #
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_DEFAULT, default=None, null=True)
    allow_opportunity_charging = models.BooleanField(default=None, null=False)


class EnumVoltageLevel(models.TextChoices):
    VOLTAGE_HV = "HV"
    VOLTAGE_HV_MV = "HV_MV"
    VOLTAGE_MV = "MV"
    VOLTAGE_MV_LV = "MV_LV"
    VOLTAGE_LV = "LV"


class EnumChargeType(models.TextChoices):
    DEPOT = "DEPOT"
    OPPORTUNITY = "OPPORTUNITY"


class Station(models.Model):
    class Meta:
        db_table = 'Station'

    # Map Engine models need geom and name as first columns
    geom = models.PointField(dim=2, srid=4326)  # without z elevation
    name = models.TextField(null=False)
    name_short = models.TextField(null=True, blank=True)
    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)

    is_electrified = models.BooleanField(default=False)
    charge_type = models.CharField(
        max_length=11, choices=EnumChargeType.choices, null=True, default=None
    )
    voltage_level = models.CharField(
        max_length=5, choices=EnumVoltageLevel.choices, null=True, default=None
    )
    amount_charging_places = models.IntegerField(default=0, null=True)
    power_per_charger = models.FloatField(default=None, null=True)
    power_total = models.FloatField(default=None, null=True)

    def save(self, *args, **kwargs):
        # Override save to make certain name_short exists
        if not self.name_short:
            self.name_short = self.name
        if self.is_electrified:
            if self.voltage_level is None or self.charge_type is None:
                error_text = "An electrified station needs a voltage level and a charge type"
                raise AttributeError(error_text)
        super().save(*args, **kwargs)

    def __str__(self):
        if not self.is_electrified:
            return f"{self.name} is not electrified. Location: {self.geom.x} {self.geom.y}"
        return (
            f"{self.name} with {self.amount_charging_places} chargers with "
            f"{self.power_per_charger} kW per charger and a total power of {self.power_total} "
            f"kW. \nLocation: {self.geom.x} {self.geom.y}"
        )


class Line(models.Model):
    class Meta:
        db_table = 'Line'

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    name = models.TextField(default=None, null=False, blank=True)
    name_short = models.TextField(default=None, null=True, blank=True)


class Route(models.Model):
    class Meta:
        db_table = 'Route'
        # TODO: We should do a check here to make sure that if a geometry is provided, it's length
        # matches the distance field. In raw SQL: "ST_Length(geom) = distance". Not sure how to
        # do this in Django though.

    # Shape of the route with height data
    geom = models.LineStringField(dim=3, srid=4326, null=True)
    distance = models.FloatField(default=None, null=False)

    name = models.TextField(default=None, null=False, blank=True)
    name_short = models.TextField(default=None, null=True, blank=True)
    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    line = models.ForeignKey(Line, null=True, on_delete=models.CASCADE)
    headsign = models.TextField(default=None, null=True, blank=True)

    departure_station = models.ForeignKey(
        Station, on_delete=models.CASCADE, related_name="route_departure_set"
    )

    arrival_station = models.ForeignKey(
        Station, on_delete=models.CASCADE, related_name="route_arrival_set"
    )

class EnumTripType(models.TextChoices):
    EMPTY_TRIP = "EMPTY"
    PASSENGER_TRIP = "PASSENGER"


class Trip(models.Model):
    class Meta:
        db_table = 'Trip'

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    route = models.ForeignKey(Route, null=False, on_delete=models.CASCADE)

    rotation = models.ForeignKey(
        Rotation, on_delete=models.CASCADE
    )  # TODO do all ForeignKeys need cascade?

    departure_time = models.DateTimeField(blank=False)

    arrival_time = models.DateTimeField(blank=False)

    # Is the Trip empty, i.e., without passengers
    type = models.CharField(
        max_length=9, choices=EnumTripType.choices, default=EnumTripType.PASSENGER_TRIP
    )

    level_of_loading = models.FloatField(default=None, null=True)

    # If time resolution is minutes, there might be trips with 0 minutes duration. To resolve
    # division by 0, we use a minimal duration of 60 seconds
    @property
    def duration_in_seconds(self):
        """duration of the trip in seconds

        duration has a minimal value of 60 seconds to avoid division by 0 errors"""

        return max(
            (self.arrival_time - self.departure_time).total_seconds(), MINIMAL_TRIP_DURATION_S
        )

    @property
    def speed(self):
        """speed in distance unit per second.

        uses property of duration_in_seconds which has a minimal value of 1"""
        return self.distance / self.duration_in_seconds

    @property
    def incline(self):
        """incline in z units per distance units

        Minimal value for distance is set to 1 to avoid division by 0."""
        return (self.arrival_station.geom.z - self.departure_station.geom.z) / max(self.distance, 1)


class StopTime(models.Model):
    class Meta:
        db_table = 'StopTime'

    """Intermediate stops of trips,
    which are not described by the arrival or departure of the trip"""

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)



    # When does the trip arrive at this station
    arrival_time = models.DateTimeField()

    # How long does the trip stop at this station
    dwell_duration = models.DurationField(null=False, default=timedelta(seconds=0))

    trip = models.ForeignKey(Trip, on_delete=models.CASCADE)
    station = models.ForeignKey(Station, on_delete=models.CASCADE)


class Area(models.Model):
    class Meta:
        db_table = 'Area'

    pass


class Event(models.Model):
    class Meta:
        db_table = 'Event'

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    vehicle_type = models.ForeignKey(VehicleType, null=False, on_delete=models.CASCADE)
    vehicle = models.ForeignKey(Vehicle, null=True, on_delete=models.CASCADE)

    #
    station = models.ForeignKey(Station, null=True, on_delete=models.CASCADE)
    trip = models.ForeignKey(Trip, null=True, on_delete=models.CASCADE)
    area = models.ForeignKey(Area, null=True, on_delete=models.CASCADE)

    subloc_no = models.IntegerField(null=True, blank=True)
    time_start = models.DateTimeField()
    time_end = models.DateTimeField()

    soc_start = models.FloatField()
    soc_end = models.FloatField()

    timeseries = models.JSONField(default=dict, null=True)

    def save(self, *args, **kwargs):
        # Exactly one of the following has to be non-null
        if (self.station is not None) + (self.trip is not None) + (self.area is not None) != 1:
            raise AttributeError(
                "An Event can only have ONE of the following Attributes.\n" "Station\nTrip\nArea"
            )
        mandatory_fields = ["time", "soc"]
        for f in mandatory_fields:
            if f not in self.timeseries.keys():
                raise AttributeError(
                    f"A dictionary key of {f} with values of {f} has to be "
                    f"provided to the json field timeseries."
                )

        data_length = len(self.timeseries[mandatory_fields[0]])
        for f in mandatory_fields:
            if len(self.timeseries[f]) != data_length:
                raise AttributeError(
                    f"The timeseries of {mandatory_fields[0]} and {f} have "
                    f"different lengths which is not allowed"
                )
        super().save(*args, **kwargs)
