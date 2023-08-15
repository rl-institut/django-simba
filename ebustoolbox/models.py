from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.gis.db import models
from django.dispatch import receiver
from django.contrib.postgres.fields import ArrayField

from pathlib import Path




class Scenario(models.Model):
    name = models.CharField(max_length=100, blank=False)
    opps_charging_power = models.FloatField(default=None, null=True, blank=True)
    deps_charging_power = models.FloatField(default=None, null=True, blank=True)

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
    # TODO add charging curve & v2g curve

    # SOC, ChargingPower
    charging_curve = ArrayField(ArrayField(models.FloatField(), size=2))
    v2g_curve = ArrayField(ArrayField(models.FloatField(), size=2), null=True)

    v2g = models.BooleanField(default=False)

    # TODO link to consumption table if no value is given here?
    consumption = models.FloatField(default=None, null=True)
    length = models.FloatField(default=None, null = True)


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


class VehicleProperties(models.Model):
    date = models.DateTimeField()
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE)
    soc = models.FloatField(null=True)
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE)


class EbusToolboxTimeseries(models.Model):
    date = models.DateTimeField(default=None)
    soc = models.FloatField()

    class Meta:
        ordering = ('date',)


class Rotation(models.Model):
    name = models.CharField(max_length=100, blank=False)
    # TODO on delete concept? also depends on if vehicle class is tied to scenario
    vehicle_class = models.ForeignKey(VehicleClass, on_delete=models.SET_DEFAULT, default=VehicleClass.get_default_pk)
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
    power_per_charger = models.FloatField(default = None, null = True)
    total_power = models.FloatField(default = None, null = True)


class Trip(models.Model):
    rotation = models.ForeignKey(Rotation, on_delete=models.CASCADE)  # TODO do all ForeignKeys need cascade?
    departure_stop = models.ForeignKey(Station, on_delete=models.CASCADE,  related_name="trip_departure_set")
    departure_time = models.DateTimeField(blank=False)
    arrival_stop = models.ForeignKey(Station, on_delete=models.CASCADE,  related_name="trip_arrival_set")
    arrival_time = models.DateTimeField(blank=False)
    distance = models.FloatField()

    # ToDo do we want a line object?
    line = models.CharField(max_length=100, blank=True, null=True)
    temperature = models.FloatField(default=None, null = True)
    level_of_loading = models.FloatField(default=None, null = True)
    # If time resolution is minutes, there might be trips with 0 minutes duration. To resolve
    # division by 0, we use a minimal duration of 1 second
    @property
    def duration_in_seconds(self):
        return min((self.arrival_time-self.departure_time).total_seconds(),1)
    @property
    def speed(self):
        return self.distance/self.duration_in_seconds

    @property
    def incline(self):
        return (self.departure_stop.geom.z-self.arrival_stop.geom.z)/min(self.distance,1)


#
# # Create your models here.
# class EbusToolbox(models.Model):
#     # title = models.TextField()
#     # ToDo Should not be in project
#     upload_path = "uploads/"
#     task_id = models.TextField(blank=True)
#     output_directory = models.TextField(default="ebustoolbox/static/data/sim_outputs/", blank=True)
#     input_schedule = models.FileField(upload_to=upload_path, default="data/examples/trips_example.csv", blank=True)
#     electrified_stations = models.FileField(upload_to=upload_path, default="data/examples/electrified_stations.json", blank=True)
#     vehicle_types = models.FileField(upload_to=upload_path, default="data/examples/vehicle_types.json",  blank=True)
#     station_data_path = models.FileField(upload_to=upload_path,  default="data/examples/all_stations.csv", blank=True)
#     outside_temperature_over_day_path = models.FileField(upload_to=upload_path, default="data/examples/default_temp_winter.csv", blank=True)
#     level_of_loading_over_day_path = models.FileField(upload_to=upload_path,  default="data/examples/default_level_of_loading_over_day.csv",blank=True)
#     cost_parameters_file = models.FileField(upload_to=upload_path,  default="data/examples/cost_params.json",blank=True)
#
#     class ExecutionMode(models.TextChoices):
#         SIM = 'sim', _('Simulation')
#         REPORT = 'report', _('Report')
#
#     modes = ArrayField(models.CharField(max_length=10)),
#
#     ##### Physical setup of environment #####
#     # preferred_charging_type = models.Choices()
#     # Default max power [kW] of grid connectors at depot and opp stations,
#     # Individual gc_power per gc can be defined in electrified stations
#     # For unlimited gc power: set very large value (default: 100000)
#     gc_power_opps = models.DecimalField(max_digits=10, decimal_places=2, default=100000)
#     gc_power_deps = models.DecimalField(max_digits=10, decimal_places=2, default=100000)
#     # Default max power [kW] of charging station at depot and opp stations (default at opps: 300)
#     # At depot stations opp and depot busses have distinct charging stations (all deps default to: 150)
#     # Individual cs_power per gc and cs type can be defined in electrified stations
#     cs_power_opps = models.DecimalField(max_digits=10, decimal_places=2, default=300)
#     cs_power_deps_depb = models.DecimalField(max_digits=10, decimal_places=2, default=150)
#     cs_power_deps_oppb = models.DecimalField(max_digits=10, decimal_places=2, default=150)
#     # Set minimum allowed state of charge when leaving depot and opportunity stations (both default: 1)
#     desired_soc_deps = models.DecimalField(max_digits=10, decimal_places=2, default=1)
#     desired_soc_opps = models.DecimalField(max_digits=10, decimal_places=2, default=1)
#     # Minimum fraction of capacity for recharge when leaving the depot. Helps calculating the
#     # Minimum standing time at depot. Between 0 - 1. (default: 1)
#     min_recharge_deps_oppb = models.DecimalField(max_digits=10, decimal_places=2, default=1)
#     min_recharge_deps_depb = models.DecimalField(max_digits=10, decimal_places=2, default=1)
#     # Min charging time at depots and opp stations in minutes (default: 0)
#     min_charging_time = models.DecimalField(max_digits=10, decimal_places=2, default=0)
#     # Buffer time in min at opp station if no specific buffer time is given in electrified_stations.json
#     # Time specific buffer times can be set via a dict e.g.: {"10-22": 5, "else": 2}
#     # NOTE: else clause is a MUST! The buffer time is deducted off of the planned standing time.
#     # It may resemble things like delays and/or docking procedures (default: 0)
#     default_buffer_time_opps = models.DecimalField(max_digits=10, decimal_places=2, default=0)
#
#     # Default voltage level for charging stations if not set in electrified_stations file
#     # Options: HV, HV/MV, MV, MV/LV, LV (default: MV)
#     # default_voltage_level = models.Choices()
#
#     # output_directory = models.FileField(upload_to="outputs/")
#
#     def to_args(self):
#         serialized_data = serialize('python', [self])[0]['fields']
#
#         # Decimal values to  float conversion
#         for key, value in serialized_data.items():
#             if isinstance(value, Decimal):
#                 serialized_data[key] = float(value)
#         return serialized_data
