from pathlib import Path

from django.conf import settings
from django.contrib.gis.db import models
from django.contrib.postgres.fields import ArrayField
from django.dispatch import receiver


class Scenario(models.Model):
    name = models.CharField(max_length=100, blank=False)

    created = models.DateTimeField(auto_now_add=True)
    task_id = models.TextField(default=None, null=True, blank=True)
    finished = models.DateTimeField(default=None, null=True, blank=True)
    options = models.JSONField(default=dict)


class UploadedFile(models.Model):
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE)
    file = models.FileField(upload_to=settings.UPLOAD_PATH)


@receiver(models.signals.pre_delete, sender=UploadedFile)
def auto_delete_file_on_delete(sender, instance, **kwargs):
    if instance.file:
        path = Path(instance.file.path)
        if path.exists():
            path.unlink()


class VehicleClass(models.Model):
    name = models.CharField(max_length=100, blank=False)

    # TODO do vehicle classes need to be connected to a scenario?

    @classmethod
    def get_default_pk(cls):
        vehicle_class, created = cls.objects.get_or_create(
            name='SB',  # TODO necessary? better default?
        )
        return vehicle_class.pk


class VehicleType(models.Model):
    name = models.CharField(max_length=100, blank=False)
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE)
    vehicle_class = models.ForeignKey(VehicleClass, on_delete=models.CASCADE)
    flex_charging = models.BooleanField()
    battery_capacity = models.FloatField()
    charging_efficiency = models.FloatField(default=0.95)
    minimum_charging_power = models.FloatField(default=0)

    # SOC, ChargingPower
    charging_curve = ArrayField(ArrayField(models.FloatField(), size=2))
    v2g_curve = ArrayField(ArrayField(models.FloatField(), size=2), null=True)

    v2g = models.BooleanField(default=False)

    # TODO link to consumption table if no value is given here?
    consumption = models.FloatField(default=None, null=True)
    length = models.FloatField(default=None, null=True)


class Vehicle(models.Model):
    name = models.CharField(max_length=100, blank=False)
    vehicle_type = models.ForeignKey(VehicleType, on_delete=models.CASCADE, null=True, blank=True)
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE)

    # ToDo insert output here or create other Class "VehicleOutput" which also contains the
    # simulation results regarding this vehicle

    # date_time_stamps = ArrayField(models.DateTimeField( default = None, null=True))
    # socs = ArrayField(models.FloatField( default = None, null=True))
    def __str__(self):
        return self.name


# ToDo Deprecated
class VehicleProperties(models.Model):
    date = models.DateTimeField()
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE)
    soc = models.FloatField(null=True)
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE)


# ToDo Deprecated
class EbusToolboxTimeseries(models.Model):
    date = models.DateTimeField(default=None)
    soc = models.FloatField()

    class Meta:
        ordering = ('date',)


class Rotation(models.Model):
    name = models.CharField(max_length=100, blank=False)
    # TODO on delete concept? also depends on if vehicle class is tied to scenario
    vehicle_class = models.ForeignKey(VehicleClass, on_delete=models.SET_DEFAULT,
                                      default=VehicleClass.get_default_pk)
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE)


class Station(models.Model):
    # Map Engine models need geom and name as first columns
    geom = models.PointField(dim=3, srid=4326)  # with z elevation
    name = models.TextField()

    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE)

    VOLTAGE_LEVEL_CHOICES = ["HV", "HV/MV", "MV", "MV/LV", "LV"]
    CHARGE_TYPES = (("oppb", "Opportunity"), ("depb", "Depot"))

    is_electrified = models.BooleanField(default=False)
    charge_type = models.CharField(max_length=4, choices=CHARGE_TYPES, null=True)
    voltage_level = models.CharField(max_length=5, choices=[(c, c) for c in VOLTAGE_LEVEL_CHOICES],
                                     null=True)
    amount_charging_places = models.IntegerField(default=0, null=True)
    power_per_charger = models.FloatField(default=None, null=True)
    total_power = models.FloatField(default=None, null=True)


class Trip(models.Model):
    rotation = models.ForeignKey(Rotation,
                                 on_delete=models.CASCADE)  # TODO do all ForeignKeys need cascade?
    departure_stop = models.ForeignKey(Station, on_delete=models.CASCADE,
                                       related_name="trip_departure_set")
    departure_time = models.DateTimeField(blank=False)
    arrival_stop = models.ForeignKey(Station, on_delete=models.CASCADE,
                                     related_name="trip_arrival_set")
    arrival_time = models.DateTimeField(blank=False)
    distance = models.FloatField()

    # ToDo do we want a line object?
    line = models.CharField(max_length=100, blank=True, null=True)
    temperature = models.FloatField(default=None, null=True)
    level_of_loading = models.FloatField(default=None, null=True)

    # If time resolution is minutes, there might be trips with 0 minutes duration. To resolve
    # division by 0, we use a minimal duration of 1 second
    @property
    def duration_in_seconds(self):
        """duration of the trip in seconds

        duration has a minimal value of 1 to avoid division by 0 errors"""

        return max((self.arrival_time - self.departure_time).total_seconds(), 1)

    @property
    def speed(self):
        """speed in distance unit per second.

        uses property of duration_in_seconds which has a minimal value of 1"""
        return self.distance / self.duration_in_seconds

    @property
    def incline(self):
        """incline in z units per distance units

        Minimal value for distance is set to 1 to avoid division by 0."""
        return (self.arrival_stop.geom.z - self.departure_stop.geom.z) / max(self.distance, 1)
