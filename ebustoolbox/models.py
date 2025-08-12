from datetime import timedelta, datetime

from django.core.validators import MinValueValidator, MaxValueValidator
from fast_update.query import FastUpdateManager
from functools import partial
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from scipy.spatial._qhull import QhullError
import shutil
import warnings

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.gis.db import models
from django.contrib.postgres.fields import ArrayField
from django.db.models import QuerySet, Sum, Case, When, Value, IntegerField, Func, F
from django.db.models.functions import Now, Length
from django.dispatch import receiver
from django.utils.timezone import make_aware

from ebus_map.managers import MVTManager, X, Y
from simba.ids import INCLINE, LEVEL_OF_LOADING, SPEED, T_AMB, CONSUMPTION

MINIMAL_TRIP_DURATION_S = 60  # seconds


class EnumScenarioType(models.TextChoices):
    SOURCE = "SOURCE"
    MUTATION = "MUATION"
    SIMULATION = "SIMULATION"


class EnumSimulationType(models.TextChoices):
    # Default simulation type with typical consumptions
    DEFAULT = "default"
    # Simulation for sizing of equipment, e.g. with extreme consumptions
    SIZING = "sizing"
    # Other


class SimulationType(models.Model):
    """Defines the type of a Simulation scenario"""

    scenario = models.ForeignKey("Scenario", on_delete=models.CASCADE)
    sim_type = models.CharField(max_length=20, choices=EnumSimulationType.choices)


class Scenario(models.Model):
    """
    Model representing a scenario in the application.

    Attributes:
        name (str): The name of the scenario. Required and cannot be blank.
        name_short (str, optional): A short name for the scenario. Can be blank.
        parent (Scenario, optional): A reference to the parent scenario, if applicable.
        scenario_type (str, optional): The type of the scenario indicating what data it contains.
        description (str, optional): A description for the scenario.
        created (datetime): The date and time when the scenario was created.
        task_id (UUID, optional): Unique identifier for the scenario's task. Can be null.
        finished (datetime, optional): The date and time when the scenario was finished, if applicable.
        simba_options (dict, optional): JSON field for Simba options. Defaults to an empty dictionary.
        eflips_depot_options (dict, optional): JSON field for eFlips Depot options. Defaults to an empty dictionary.

    Meta:
        db_table (str): The name of the database table for this model (set to "Scenario").

    Methods:
        get_default_pk(cls): Class method to retrieve the primary key of the default scenario.
                            If not exists, creates a default scenario named "default_scenario".

    Usage Example:
        To retrieve the default scenario's primary key:
        >>> default_scenario_pk = Scenario.get_default_pk()
    """

    class Meta:
        db_table = "Scenario"

    name = models.TextField(blank=False)
    name_short = models.TextField(blank=True, null=True)
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True)

    scenario_type = models.CharField(choices=EnumScenarioType.choices, null=True)
    description = models.TextField(blank=True, null=True)

    created = models.DateTimeField(
        auto_now_add=True, db_default=Now()
    )  # Set to now() on the database side
    task_id = models.UUIDField(default=None, null=True, unique=True)
    finished = models.DateTimeField(default=None, null=True, blank=True)
    simba_options = models.JSONField(default=dict, null=True)
    eflips_depot_options = models.JSONField(default=dict, null=True)

    manager = models.ForeignKey(
        User, on_delete=models.SET_NULL, default=None, null=True, blank=True, related_name="+"
    )

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
            shutil.rmtree((Path(settings.UPLOAD_PATH) / str(instance.task_id)))
        except FileNotFoundError:
            # The Folder does not exist. That is not a problem
            pass


class UserGroup(models.Model):
    """
    Defines who has access to a scenario.

    If not outright a scenario's manager, one has to be part of a UserGroup to access a project.
    Attributes:
    - name (string): displayed name
    - users (M2M User): participants of group
    - scenarios (M2M Scenario): shared scenarios
    """

    name = models.TextField(blank=False)
    users = models.ManyToManyField(User)
    scenarios = models.ManyToManyField(Scenario)


class UploadedFile(models.Model):
    """
    Model representing an uploaded file associated with a scenario.

    Attributes:
        scenario (Scenario): The scenario to which the file is associated. Foreign key to the Scenario model.
        file (FileField): The actual file field storing the uploaded file, with the specified upload path.

    Usage Example:
        To create a new UploadedFile instance and associate it with a scenario:
        >>> scenario_instance = Scenario.objects.get(id=1)
        >>> uploaded_file_instance = UploadedFile(scenario=scenario_instance, file=my_file)
        >>> uploaded_file_instance.save()
    """

    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE)
    file = models.FileField(upload_to=settings.UPLOAD_PATH)


@receiver(models.signals.pre_delete, sender=UploadedFile)
def auto_delete_file_on_delete(sender, instance, **kwargs):
    if instance.file:
        path = Path(instance.file.path)
        if path.exists():
            path.unlink()


class BatteryType(models.Model):
    """
    Model representing a type of battery associated with a scenario.

    Attributes:
        scenario (Scenario): The scenario to which the battery type is associated. Foreign key to the Scenario model.
        specific_mass (float): The specific mass of the battery relative to gross capacity.
                              Cannot be null, but can be blank.
        chemistry (dict): The chemistry of the battery, defined in eFLIPS-LCA.
                          Defaults to an empty dictionary.

    Meta:
        db_table (str): The name of the database table for this model (set to "BatteryType").

    Usage Example:
        To create a new BatteryType instance and associate it with a scenario:
        >>> scenario_instance = Scenario.objects.get(id=1)
        >>> battery_type_instance = BatteryType(scenario=scenario_instance,
        ... specific_mass=2.5, chemistry={"type": "Li-ion"})
        >>> battery_type_instance.save()
    """

    class Meta:
        db_table = "BatteryType"

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)

    # relative to gross capacity
    specific_mass = models.FloatField(null=False, blank=True)
    # defined in eFLIPS-LCA
    chemistry = models.JSONField(null=False, default=dict)


class AssocVehicleTypeVehicleClass(models.Model):
    """
    This model is used to store the many-to-many relationship between VehicleType and VehicleClass.
    """

    class Meta:
        db_table = "AssocVehicleTypeVehicleClass"

    vehicle_type = models.ForeignKey("VehicleType", on_delete=models.CASCADE)
    vehicle_class = models.ForeignKey("VehicleClass", on_delete=models.CASCADE)


class VehicleType(models.Model):
    """
    Model representing a type of vehicle associated with a scenario.

    Attributes:
        scenario (Scenario): The scenario to which the vehicle type is associated. Foreign key to
                             the Scenario model.
        battery_type (BatteryType): The type of battery associated with the vehicle type.
                                    Can be null. Foreign key to the BatteryType model.
        name (str): The name of the vehicle type. Cannot be null or blank.
        name_short (str, optional): A short name for the vehicle type. Defaults to the full name if
                                    not provided.
        opportunity_charging_capable (bool): Indicates whether the vehicle type is capable of
                                             opportunity charging.
        battery_capacity (float): The battery capacity of the vehicle type in [kWh].
        battery_capacity_reserve (float): The reserve capacity of the battery in [kWh].
                                          Defaults to 0.
        charging_efficiency (float): The charging efficiency of the vehicle type between 0 and 1.
                                     Defaults to 0.95.
        minimum_charging_power (float): The minimum charging power required in [kW]. Defaults to 0.
        charging_curve (ArrayField): Array field representing the State of Charge (SOC) and Charging
                                     Power curve in [-] and [kW].
        v2g_curve (ArrayField, optional): Array field representing the Vehicle-to-Grid (V2G) curve
                                          in [-] and [kW]. Can be null.
        consumption (float, optional): The consumption of the vehicle type in [kWh/km].
                                       Defaults to None.
        length (float, optional): The length of the vehicle in [m]. Defaults to None.
        width (float, optional): The width of the vehicle in [m]. Defaults to None.
        height (float, optional): The height of the vehicle in [m]. Defaults to None.
        empty_mass (float, optional): The empty mass of the vehicle in [kg]. Defaults to None.
        allowed_mass (float, optional): The allowed mass of the vehicle in [kg]. Defaults to None.
        vehicle_classes (ManyToManyField): Many-to-Many relationship with VehicleClass
                                           through AssocVehicleTypeVehicleClass.

    Meta:
        db_table (str): The name of the database table for this model (set to "VehicleType").

    Usage Example:
        To create a new VehicleType instance and associate it with a scenario:
        >>> scenario_instance = Scenario.objects.get(id=1)
        >>> battery_type_instance = BatteryType.objects.get(id=1)
        >>> vehicle_type_instance = VehicleType(
        ...     scenario=scenario_instance,
        ...     battery_type=battery_type_instance,
        ...     name="Electric Car",
        ...     opportunity_charging_capable=True,
        ...     battery_capacity=60.0,
        ...     charging_curve=[[0.2, 50], [0.5, 100], [0.8, 150]],
        ... )
        >>> vehicle_type_instance.save()
    """

    class Meta:
        db_table = "VehicleType"

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    battery_type = models.ForeignKey(BatteryType, null=True, on_delete=models.CASCADE)

    name = models.TextField(null=False, blank=False)
    name_short = models.TextField(null=True, blank=False, default=name)
    opportunity_charging_capable = models.BooleanField()
    battery_capacity = models.FloatField(
        validators=[MinValueValidator(0), MaxValueValidator(1000000)]
    )
    battery_capacity_reserve = models.FloatField(default=0, db_default=0)
    charging_efficiency = models.FloatField(default=0.95, db_default=0.95)
    minimum_charging_power = models.FloatField(default=0, db_default=0)

    # SOC, ChargingPower
    charging_curve = ArrayField(ArrayField(models.FloatField(), size=2))
    v2g_curve = ArrayField(ArrayField(models.FloatField(), size=2), null=True)

    # Possible constant value for average consumption
    consumption = models.FloatField(default=None, null=True)
    # Possible constant value for extreme/max consumption
    max_consumption = models.FloatField(default=None, null=True)

    # Shape of the vehicle in the form of length, width, height.
    length = models.FloatField(default=None, null=True)
    width = models.FloatField(default=None, null=True)
    height = models.FloatField(default=None, null=True)

    # Including battery and driver, no passengers
    empty_mass = models.FloatField(default=None, null=True)
    allowed_mass = models.FloatField(default=None, null=True)

    vehicle_classes = models.ManyToManyField("VehicleClass", through="AssocVehicleTypeVehicleClass")
    """Vehicle classes this vehicle type belongs to."""

    def save(self, *args, **kwargs):
        # Override save to make certain name_short exists
        if not self.name_short or self.name_short == str(models.TextField(null=False, blank=False)):
            self.name_short = self.name
        super().save(*args, **kwargs)


class VehicleClass(models.Model):
    """
    Model representing a class of vehicles associated with a scenario.

    Attributes:
        scenario (Scenario): The scenario to which the vehicle class is associated.
                             Foreign key to the Scenario model.
        name (str): The name of the vehicle class. Cannot be null or blank.
        name_short (str, optional): A short name for the vehicle class. Can be blank.
        vehicle_types (ManyToManyField): Many-to-Many relationship with VehicleType
                                         through AssocVehicleTypeVehicleClass.

    Meta:
        db_table (str): The name of the database table for this model (set to "VehicleClass").

    Usage Example:
        To create a new VehicleClass instance and associate it with a scenario:
        >>> scenario_instance = Scenario.objects.get(id=1)
        >>> vehicle_class_instance = VehicleClass(
        ...     scenario=scenario_instance,
        ...     name="Compact Cars",
        ...     name_short="Compact",
        ... )
        >>> vehicle_class_instance.save()
    """

    class Meta:
        db_table = "VehicleClass"

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    name = models.TextField(null=False, blank=False)
    name_short = models.TextField(null=True, blank=True)

    vehicle_types = models.ManyToManyField(VehicleType, through="AssocVehicleTypeVehicleClass")
    """Vehicle types that belong to this vehicle class."""


class Consumption(models.Model):
    """
    Model representing Consumption data associated with a specific scenario.

    If Consumption is used with SimBA the vehicle_types float consumption field needs to be set to
    None. The vehicle_types are linked to this Consumption instance through a vehicle_class.
    Consumption.name needs to be unique for the linked scenario, e.g. two Consumption instances
    of the same scenario cannot share a name.
    The following columns are expected for SimBA consumption calculation.
    "incline". height-difference/Distance [-]
    "level_of_loading": loaded_mass/max. loaded mass [-]
    "mean_speed_kmh": mean speed of trip [km/h]
    "t_amb": ambient temperature [°C]

    "consumption_kwh_per_km" is expected to be passed as values.
    Passing a pandas.DataFrame for Consumption construction is possible via the Consumption.from_df
    method

    Attributes:
        name (str): name of the Consumption data, indicating its source or intention
            (e.g., 'ConsumptionData of 12m Bus'). Cannot be blank
        vehicle_class (VehicleClass): vehicle class associated with the consumption data
        scenario (Scenario): scenario to which the consumption data is associated. Foreign key
                             to the Scenario model
        columns (list): list of column names representing input conditions for consumption calculation
        data_points (list): list of lists representing input data points for consumption calculation
        values (list): list of consumption values corresponding to the data points in kwh/km
        linear_interpolator (function): function for linear interpolation of consumption data
        nearest_interpolator (function):function for nearest interpolation of consumption data
        one_dim (bool): Indicates if the consumption data is one-dimensional

    """

    name = models.CharField(max_length=100)
    vehicle_class = models.ForeignKey(VehicleClass, null=False, on_delete=models.CASCADE)
    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    columns = models.JSONField(default=list, null=False)
    data_points = ArrayField(ArrayField(models.FloatField(), size=None), size=None, null=False)
    values = ArrayField(models.FloatField(), size=None, null=False)

    linear_interpolator = None
    nearest_interpolator = None
    one_dim = False

    class Meta:
        db_table = "ConsumptionLut"

    def __str__(self):
        avg = np.array(self.values).mean()
        return f"Consumption table {self.name} with average consumption of {avg:.1f} "

    def to_simba_name(self) -> str:
        """Create a verbose unique name for simba"""
        return self.name + "_" + str(self.id)

    @classmethod
    def get_id_from_simba_name(cls, name) -> int:
        """Return the id of a verbose unique name from simba"""
        return int(name.split("_")[-1])

    def to_df(self):
        """
        Convert consumption data to a pandas DataFrame.

        :return: DataFrame containing consumption data.
        :rtype: pandas.DataFrame
        """
        return pd.DataFrame(
            columns=self.columns + ["consumption_kwh_per_km"],
            data=np.hstack((np.array(self.data_points), np.expand_dims(self.values, axis=1))),
        )

    @staticmethod
    def from_df(df, name="My_Consumption") -> "Consumption":
        """Create a Consumption object from a pandas DataFrame.

        To use the Consumption in a scenario, link it with a scenario and vehicle_class.
        A vehicle_class should only be pointed at by one consumption.
        Vehicle_types which should use this Consumption need to be linked with the vehicle_class,
        e.g. vehicle_class.add(vehicle_type).
        Make sure the vehicle_type has a consumption of None. Only in this case the Consumption
        interpolation is used..
        """

        for expected_col in [INCLINE, T_AMB, LEVEL_OF_LOADING, SPEED, CONSUMPTION]:
            assert expected_col in df.columns, f"Consumption data is missing {expected_col}"

        columns = [INCLINE, T_AMB, LEVEL_OF_LOADING, SPEED]
        data_points = np.array(df.loc[:, columns].values).tolist()
        values = np.array(df.loc[:, CONSUMPTION].values).tolist()
        # Consumption is returned without scenario and vehicle_class.
        # This needs to be patched in before the Consumption can be saved.
        return Consumption(
            name=name,
            columns=columns,
            data_points=data_points,
            values=values,
        )

    def get_consumption(self, input_point: dict | list) -> float:
        """
        Get the consumption for a given input point.

        :param input_point: Input conditions for consumption calculation
        :type input_point: dict | list
        :return: Consumption value
        :rtype: float
        :raises ValueError: if input_point does not contain exactly the needed columns
        """
        point = input_point
        if isinstance(input_point, dict):
            if not set(input_point.keys()) == set(self.columns):
                error = f"{input_point} does not contain exactly the needed columns:{self.columns}"
                raise ValueError(error)
            point = [input_point[key] for key in self.columns]

        if self.linear_interpolator is None or self.nearest_interpolator is None:
            self._set_interpolators()

        output = self.linear_interpolator(point)
        if np.isnan(output):
            output = self.nearest_interpolator(point)
        return output

    def _set_interpolators(self):
        """
        Set interpolation functions for consumption data.
        """
        if self._dims() == 1:
            data = np.array(self.data_points).squeeze()

            def partial_func(point):
                return np.interp(point, data, self.values)

            self.linear_interpolator = partial_func

            def find_nearest(value):
                array = np.asarray(data.squeeze())
                idx = (np.abs(array - value)).argmin()
                return self.values[idx]

            self.nearest_interpolator = find_nearest
            return
        try:
            self.linear_interpolator = LinearNDInterpolator(self.data_points, self.values)
        except QhullError:
            warnings.warn(
                "Consumption table does not contain enough elements for multidimensional"
                " linear interpolation. Nearest Interpolation is used instead."
            )
            self.linear_interpolator = NearestNDInterpolator(self.data_points, self.values)
        self.nearest_interpolator = NearestNDInterpolator(self.data_points, self.values)

    def _dims(self):
        return np.array(self.data_points).shape[1]

    def get_columns(self):
        """
        Get the column names of the consumption data.

        :return: Column names of the consumption data.
        :rtype: list
        """
        return self.columns

    def save(self, *args, **kwargs):
        if len(self.columns) > 1 and len(self.data_points) != len(self.values):
            error = "Consumption table does not have the same amount of data_points and values"
            raise AttributeError(error)
        if len(self.columns) == 1:
            data = np.array(self.data_points)
            if len(data.shape) == 1:
                # Transform a list like [1,2,3] to [[1],[2],[3]]
                self.data_points = np.expand_dims(self.data_points, 0).T.tolist()
        self._set_interpolators()
        super().save(*args, **kwargs)


class Vehicle(models.Model):
    """
    Model representing a vehicle in a scenario.

    Attributes:
        scenario (Scenario): The scenario to which the vehicle is associated.
                             Foreign key to the Scenario model.
        name (str): The name of the vehicle. Cannot be null or blank.
        name_short (str, optional): A short name for the vehicle. Can be blank. If not provided,
                                    defaults to the full name.
        vehicle_type (VehicleType): The type of the vehicle. Foreign key to the VehicleType model.

    Meta:
        db_table (str): The name of the database table for this model (set to "Vehicle").

    Methods:
        save(self, *args, **kwargs): Override of the save method to ensure the existence of
                                     name_short.

    Usage Example:
        To create a new Vehicle instance and associate it with a scenario:
        >>> scenario_instance = Scenario.objects.get(id=1)
        >>> vehicle_type_instance = VehicleType.objects.get(id=1)
        >>> vehicle_instance = Vehicle(
        ...     scenario=scenario_instance,
        ...     name="Electric Car 1",
        ...     vehicle_type=vehicle_type_instance,
        ... )
        >>> vehicle_instance.save()
    """

    class Meta:
        db_table = "Vehicle"

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)

    name = models.TextField(null=False, blank=False)
    name_short = models.TextField(null=True, blank=True)
    vehicle_type = models.ForeignKey(VehicleType, on_delete=models.CASCADE, null=False, blank=True)

    def save(self, *args, **kwargs):
        # Override save to make certain name_short exists
        if not self.name_short:
            self.name_short = self.name
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    def to_simba_name(self):
        ct = EnumChargeType.DEPOT.value
        if self.vehicle_type.opportunity_charging_capable:
            ct = EnumChargeType.OPPORTUNITY.value
        return str(self.vehicle_type.id) + "_" + ct + "_" + str(self.pk)


class Rotation(models.Model):
    """
    Model representing a rotation associated with a scenario.

    Attributes:
        scenario (Scenario): The scenario to which the rotation is associated.
                             Foreign key to the Scenario model.
        name (str, optional): The name of the rotation. Can be null, but cannot be blank.
        vehicle_type (VehicleType): The type of vehicle associated with the rotation.
                                    Foreign key to the VehicleType model.
        vehicle (Vehicle, optional): The specific vehicle associated with the rotation.
                                     Defaults to None.
        allow_opportunity_charging (bool): Indicates whether opportunity charging is allowed for
                                           the rotation. Cannot be null.

    Meta:
        db_table (str): The name of the database table for this model (set to "Rotation").

    Usage Example:
        To create a new Rotation instance and associate it with a scenario:
        >>> scenario_instance = Scenario.objects.get(id=1)
        >>> vehicle_type_instance = VehicleType.objects.get(id=1)
        >>> rotation_instance = Rotation(
        ...     scenario=scenario_instance,
        ...     name="Morning Shift",
        ...     vehicle_type=vehicle_type_instance,
        ...     vehicle=vehicle_instance,
        ...     allow_opportunity_charging=True,
        ... )
        >>> rotation_instance.save()
    """

    class Meta:
        db_table = "Rotation"

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)

    name = models.TextField(blank=False, null=True)
    vehicle_type = models.ForeignKey(VehicleType, null=False, blank=True, on_delete=models.CASCADE)

    # SimBA specific data to make SimBA simulations reproducible
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_DEFAULT, default=None, null=True)
    allow_opportunity_charging = models.BooleanField(default=True, null=False)

    def get_distance(self):
        # get distance of this rotation in meters
        return Route.objects.filter(trip__rotation=self).aggregate(Sum("distance"))["distance__sum"]


def annotate_distance(query: QuerySet[Rotation]):
    return query.annotate(distance=Sum("trip__route__distance"))


def get_longest_distance_rotation(filter_dict: dict) -> Rotation:
    return annotate_distance(Rotation.objects.filter(**filter_dict)).order_by("distance").last()


def get_shortest_distance_rotation(filter_dict: dict) -> Rotation:
    return annotate_distance(Rotation.objects.filter(**filter_dict)).order_by("distance").first()


class EnumVoltageLevel(models.TextChoices):
    VOLTAGE_HV = "HV"
    VOLTAGE_HV_MV = "HV_MV"
    VOLTAGE_MV = "MV"
    VOLTAGE_MV_LV = "MV_LV"
    VOLTAGE_LV = "LV"


class EnumChargeType(models.TextChoices):
    DEPOT = "depb"
    OPPORTUNITY = "oppb"


def charge_type_from_simba_to_db(charge_type: str) -> str:
    if charge_type[:3].lower() == "dep":
        return EnumChargeType.DEPOT.value
    if charge_type[:3].lower() == "opp":
        return EnumChargeType.OPPORTUNITY.value
    raise Exception(f"{charge_type=} not found in EnumChargeTypes")


def charge_type_from_db_to_station(charge_type: str, is_station: bool) -> str:
    suffix = "b"
    if is_station:
        suffix = "s"
    if charge_type == EnumChargeType.DEPOT.value:
        return "dep" + suffix
    if charge_type == EnumChargeType.OPPORTUNITY.value:
        return "opp" + suffix
    raise Exception(f"{charge_type=} not found in EnumChargeTypes")


class Temperatures(models.Model):
    """
    Model representing temperature data associated with a specific scenario, allowing for datetime interpolation.

    Attributes:
        scenario (Scenario): The scenario to which the temperature data is associated. Foreign key
                             to the Scenario model.
        name (str): The name of the temperature data, indicating its source or intention
                    (e.g., 'Max. Temperatures Berlin'). Cannot be blank.
        use_only_time (bool): Determines whether the datetime should be interpreted as both date
                              and time or only time.
                              Defaults to True, indicating only time is considered.
        datetimes (list): A list of datetimes associated with temperature data. Can be null.
        data (list): A list of temperature values corresponding to the datetimes. Can be null.
        temperature_interpolation (function): Internal function for temperature interpolation.

    Methods:
        save(self, *args, **kwargs): Overrides the save method to perform data validation and
                                     initialize the interpolation function.
        get_temperature(self, datetime: datetime) -> float: Retrieves the interpolated temperature
                                                            for a given datetime.

    Functions:
        get_datetime_interpolation_function(datetimes: list, data: list) -> function:
            Generates a datetime interpolation function based on input datetimes and corresponding
            temperature data.

        assert_is_type(obj, check_type: type): Raises an AttributeError if the given object is not
                                               of the specified type.

    Meta:
        db_table (str): The name of the database table for this model (set to "Temperatures").

    Usage Example:
        To create a new Temperatures instance and associate it with a scenario, providing
        temperature data:
        >>> scenario_instance = Scenario.objects.get(id=1)
        >>> temperatures_instance = Temperatures(
        ...     scenario=scenario_instance,
        ...     name="Max. Temperatures Berlin",
        ...     use_only_time=True,
        ...     datetimes=[datetime1, datetime2],
        ...     data=[temperature1, temperature2],
        ... )
        >>> temperatures_instance.save()
        >>> interpolated_temperature = temperatures_instance.get_interpolated_temperature(datetime3)
    """

    class Meta:
        db_table = "Temperatures"

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    # Name of Temperature data, to indicate a source or intention, e.g. 'Max. Temperatures Berlin'
    name = models.TextField(blank=False)

    # Should the datetime be interpreted as datetime or only time.
    use_only_time = models.BooleanField(null=False, default=True)
    # datetimes and associated data
    datetimes = ArrayField(models.DateTimeField(), default=list)
    data = ArrayField(models.FloatField(), default=list)

    temperature_interpolation = None
    temperature_closest_function = None

    def make_aware(self):
        aware_datetimes = []
        for date_time in self.datetimes:
            aware_datetimes.append(make_aware(date_time))
        self.datetimes = aware_datetimes

    def save(self, *args, **kwargs):
        """
        Overrides the save method to perform data validation and initialize the
        interpolation function.
        """
        if self.use_only_time:
            dates = {(date.year, date.month, date.day) for date in self.datetimes}
            if len(dates) != 1:
                raise AttributeError(
                    f"Data of {self} contains multiple dates. This is not "
                    "allowed when use_only_time=True"
                )
        self.temperature_interpolation = get_datetime_interpolation_function(
            self.datetimes, self.data
        )
        self.temperature_closest_function = get_datetime_closest_function(self.datetimes, self.data)
        super().save(*args, **kwargs)

    @staticmethod
    def create_constant_temperatures(scenario, temperature: float) -> "Temperatures":
        self = Temperatures()
        self.scenario = scenario
        self.use_only_time = True
        self.datetimes = [datetime(1900, 1, 1)]
        self.data = [temperature]
        self.save()
        return self

    def get_interpolated_temperature(self, date_time: datetime) -> float:
        """
        Retrieves the interpolated temperature for a given datetime.
        """
        if self.temperature_interpolation is None:
            self.temperature_interpolation = get_datetime_interpolation_function(
                self.datetimes, self.data
            )
        if self.use_only_time:
            date = datetime.fromisoformat(str(next(iter(self.datetimes))))
            date_time = date_time.replace(year=date.year, month=date.month, day=date.day)
        return self.temperature_interpolation(date_time)

    def get_closest_temperature(self, date_time: datetime):
        """
        Retrieves the interpolated temperature for a given datetime.
        """
        if self.temperature_closest_function is None:
            self.temperature_closest_function = get_datetime_closest_function(
                self.datetimes, self.data
            )
        if self.use_only_time:
            date = date_time.fromisoformat(str(next(iter(self.datetimes))))
            date_time = date_time.replace(year=date.year, month=date.month, day=date.day)
        return self.temperature_closest_function(date_time)


def get_datetime_closest_function(datetimes: list[datetime], data: list):
    """
    Generates a function which returns the closest value based on input datetimes and corresponding data.

    Args:
        datetimes (list): A list of datetime objects.
        data (list): A list of values corresponding to the datetimes.

    Returns:
        function: A datetime interpolation function.
    """
    # In the case of a single temperature just return the temperature
    if len(data) == 1:
        return lambda _: data[0]

    # sort by key
    sort_index = np.argsort(np.array(datetimes), axis=0)
    sorted_data = (np.array([datetimes, data]).T)[sort_index]
    first_time = sorted_data[0, 0]
    # create the timedelta
    xp = sorted_data[:, 0] - first_time
    # cast the timedelta to seconds as int64
    xp = xp.astype("timedelta64[s]").view("int64")
    # cast the data to floats
    fp = sorted_data[:, 1].astype(float)
    # create the interpolation function
    partial_closest = partial(np.searchsorted, a=xp, side="left")

    def partial_closest_function(date_time: datetime) -> float:
        delta_time_as_int = (
            (np.array([date_time]) - first_time).astype("timedelta64[s]").view("int64")
        )
        # idx 1 is the index of xp, where xp[i-1] < delta_time_as_int <= xp[i]
        idx1 = partial_closest(v=delta_time_as_int)
        x1 = xp[idx1]
        idx0 = np.max((idx1 - 1, np.zeros(len(idx1))), axis=0).astype(int)
        x0 = xp[idx0]
        indicies = idx1 - (x1 - delta_time_as_int >= delta_time_as_int - x0)
        return fp[np.max((indicies, np.zeros(len(idx1))), axis=0).astype(int)].squeeze()

    return partial_closest_function


def get_datetime_interpolation_function(datetimes: list[datetime], data: list):
    """
    Generates a datetime interpolation function based on input datetimes and corresponding data.

    Args:
        datetimes (list): A list of datetime objects.
        data (list): A list of values corresponding to the datetimes.

    Returns:
        function: A datetime interpolation function.
    """
    # sort by key
    sort_index = np.argsort(np.array(datetimes), axis=0)
    sorted_data = (np.array([datetimes, data]).T)[sort_index]
    first_time = sorted_data[0, 0]
    # create the timedelta
    xp = sorted_data[:, 0] - first_time
    # cast the timedelta to seconds as int64
    xp = xp.astype("timedelta64[s]").view("int64")
    # cast the data to floats
    fp = sorted_data[:, 1].astype(float)
    # create the interpolation function
    partial_interp = partial(np.interp, xp=xp, fp=fp)

    def partial_interpolation_function(date_time: datetime) -> float:
        delta_time_as_int = (
            (np.array([date_time]) - first_time).astype("timedelta64[s]").view("int64")
        )
        delta_time_as_int = delta_time_as_int.squeeze()
        return partial_interp(delta_time_as_int)

    return partial_interpolation_function


def assert_is_type(obj, check_type: type):
    """
    Raises an AttributeError if the given object is not of the specified type.

    Args:
        obj: The object to check.
        check_type (type):
    """
    if not isinstance(obj, check_type):
        raise AttributeError(f"{obj} is not of type {check_type}")


class CountBusServices(Func):
    function = "COUNT"
    output_field = IntegerField()

    def __init__(self, **extra):
        # Call the super class constructor with F('id') as the first argument
        super().__init__(F("id"), **extra)

    def as_sql(self, compiler, connection):
        # We override the as_sql method to generate our custom SQL
        # Get the SQL representation of the first source expression, which is F('id')
        expression_sql, expression_params = self.source_expressions[0].as_sql(compiler, connection)
        sql = f'(SELECT COUNT(*) FROM public."Route" WHERE arrival_station_id = {expression_sql})'

        return sql, expression_params


class IsDepot(Func):
    function = "EXIST"

    def __init__(self, **extra):
        # Call the super class constructor with F('id') as the first argument
        super().__init__(F("id"), **extra)

    def as_sql(self, compiler, connection):
        # We override the as_sql method to generate our custom SQL
        # Get the SQL representation of the first source expression, which is F('id')
        expression_sql, expression_params = self.source_expressions[0].as_sql(compiler, connection)
        sql = f'(SELECT EXISTS (SELECT 1 FROM public."Depot" WHERE station_id = {expression_sql}))'

        return sql, expression_params


class Station(models.Model):
    """
    Model representing a station associated with a scenario.

    Attributes:
        geom (PointField): The geographic point representing the location of the station without z elevation.
                          Uses SRID 4326 for geographic coordinates.
        name (str): The name of the station. Cannot be null.
        name_short (str, optional): A short name for the station. Can be blank.
        scenario (Scenario): The scenario to which the station is associated. Foreign key to the Scenario model.
        is_electrified (bool): Indicates whether the station is electrified. Defaults to False.
        is_electrifiable (bool): Indicates whether the station could be electrified. Defaults to True.
        charge_type (str, optional): The type of charging available at the station.
                                     Choices defined by EnumChargeType. Defaults to None.
        voltage_level (str, optional): The voltage level of the station.
                                       Choices defined by EnumVoltageLevel. Defaults to None.
        amount_charging_places (int, optional): The number of charging places at the station. Defaults to 0.
        power_per_charger (float, optional): The power per charger at the station in [kW]. Defaults to None.
        power_total (float, optional): The total power capacity of the station in [kW]. Defaults to None.
        stations (ManyToManyField): Many-to-Many relationship with Route through AssocRouteStation.
                                    Stations along this route, ordered by `elapsed_distance`.

    Meta:
        db_table (str): The name of the database table for this model (set to "Station").

    Usage Example:
        To create a new Station instance and associate it with a scenario:
        >>> scenario_instance = Scenario.objects.get(id=1)
        >>> station_instance = Station(
        ...     geom=Point(x=longitude, y=latitude),
        ...     name="Charging Station 1",
        ...     name_short="CS1",
        ...     scenario=scenario_instance,
        ...     is_electrified=True,
        ...     charge_type=EnumChargeType.FAST.value,
        ...     voltage_level=EnumVoltageLevel.HIGH.value,
        ...     amount_charging_places=10,
        ...     power_per_charger=50.0,
        ...     power_total=500.0,
        ... )
        >>> station_instance.save()
    """

    class Meta:
        db_table = "Station"

    # Map Engine models need geom and name as first columns
    geom = models.PointField(dim=3, srid=4326, null=True)  # without z elevation
    name = models.TextField(null=False)
    name_short = models.TextField(null=True, blank=True)
    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)

    is_electrified = models.BooleanField(default=False)
    is_electrifiable = models.BooleanField(default=True)
    charge_type = models.CharField(
        max_length=4, choices=EnumChargeType.choices, null=True, default=None
    )
    voltage_level = models.CharField(
        max_length=5, choices=EnumVoltageLevel.choices, null=True, default=None
    )
    amount_charging_places = models.IntegerField(default=0, null=True)
    power_per_charger = models.FloatField(default=None, null=True)
    power_total = models.FloatField(default=None, null=True)

    stations = models.ManyToManyField("Route", through="AssocRouteStation")
    """Stations along this route. Ordered by `elapsed_distance`."""

    objects = FastUpdateManager()

    # Make sure all annotations are part of the columns below, if the data is supposed to be
    # delivered to the map
    annotations = {
        "center": models.functions.Centroid("geom"),
        "lat": X("center", output_field=models.DecimalField()),
        "lon": Y("center", output_field=models.DecimalField()),
        "title_length": Length("name"),
        "electrified": Case(
            When(is_electrified=True, then=Value(10)),
            default=Value(0),
            output_field=models.IntegerField(),
        ),
        "power_total_ann": F("power_total"),
        "num_arrivals": CountBusServices(),
        "is_depot": IsDepot(),
    }

    vector_tiles = MVTManager(
        geo_col="geom",
        columns=[
            "id",
            "geom",
            "name",
            "lat",
            "lon",
            "title_length",
            "electrified",
            "power_total_ann",
            "num_arrivals",
            "is_depot",
        ],
    )

    layer = "busstop"
    mapping = {
        "id": "id",
        "geom": "POINT",
        "name": "name",
        "geom_label": "geom_label",
    }

    @classmethod
    def get_popup_data(cls, id):
        # circular import
        from .util import get_charge_chart

        obj = cls.objects.get(id=id)
        data = vars(obj)
        plot = get_charge_chart(obj)
        if plot:
            data["plot"] = plot
        return data

    def is_valid(self):
        if self.is_electrified:
            if self.voltage_level is None or self.charge_type is None:
                error_text = "An electrified station needs a voltage level and a charge type"
                raise AttributeError(f"Station {self.name}:" + error_text)
            if self.voltage_level not in EnumVoltageLevel.values:
                error_text = (
                    "An electrified station needs a voltage level with one of these "
                    "values:\n" + "\n".join(EnumChargeType.values)
                )
                raise AttributeError(
                    f"Station {self.name} with {self.voltage_level}: " + error_text
                )
            if self.charge_type not in EnumChargeType.values:
                error_text = (
                    "An electrified station needs a charge type with one of these "
                    "values :\n" + "\n".join(EnumChargeType.values)
                )
                raise AttributeError(f"Station {self.name} with {self.charge_type}:" + error_text)

    def save(self, *args, **kwargs):
        # Override save to make certain name_short exists
        if not self.name_short:
            self.name_short = self.name
        self.is_valid()
        super().save(*args, **kwargs)

    def __str__(self):
        if not self.is_electrified:
            return f"{self.name} is not electrified. Location: {None if not self.geom else (self.geom.x, self.geom.y)}"
        return (
            f"{self.name} with {self.amount_charging_places} chargers with "
            f"{self.power_per_charger} kW per charger and a total power of {self.power_total} "
            f"kW. \nLocation: {None if not self.geom else (self.geom.x, self.geom.y)}"
        )

    def to_simba_name(self) -> str:
        """Create a verbose unqiue name for simba"""
        return self.name + "_" + str(self.id)

    @classmethod
    def get_id_from_simba_name(cls, name) -> int:
        """Return the id of a verbose unique name from simba"""
        return int(name.split("_")[-1])

    @classmethod
    def get_default_pk(cls):
        scenario = Scenario.objects.get(id=Scenario.get_default_pk())
        station, created = cls.objects.get_or_create(
            scenario=scenario,
            name="default_station",
        )
        return station.pk


class Line(models.Model):
    """
    Model representing a line associated with a scenario.

    Attributes:
        scenario (Scenario): The scenario to which the line is associated. Foreign key to the Scenario model.
        name (str, optional): The name of the line. Defaults to None, cannot be blank.
        name_short (str, optional): A short name for the line. Defaults to None and can be blank.

    Meta:
        db_table (str): The name of the database table for this model (set to "Line").

    Usage Example:
        To create a new Line instance and associate it with a scenario:
        >>> scenario_instance = Scenario.objects.get(id=1)
        >>> line_instance = Line(
        ...     scenario=scenario_instance,
        ...     name="Main Line",
        ...     name_short="ML",
        ... )
        >>> line_instance.save()
    """

    class Meta:
        db_table = "Line"

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    name = models.TextField(default=None, null=False, blank=True)
    name_short = models.TextField(default=None, null=True, blank=True)


class Route(models.Model):
    """
    Model representing a route associated with a scenario.

    Attributes:
        geom (LineStringField): The shape of the route with height data.
                                Uses SRID 4326 for geographic coordinates. Can be null.
        distance (float): The distance covered by the route in [m]. Cannot be null.

        name (str, optional): The name of the route. Defaults to None and cannot be blank.
        name_short (str, optional): A short name for the route. Defaults to None and can be blank.
        scenario (Scenario): The scenario to which the route is associated.
                             Foreign key to the Scenario model.
        line (Line, optional): The line associated with the route. Defaults to None.
        headsign (str, optional): The headsign or destination information for the route.
                                  Defaults to None and can be blank.
        departure_station (Station): The departure station for the route.
                                     Foreign key to the Station model.
        arrival_station (Station): The arrival station for the route.
                                   Foreign key to the Station model.

        stations (ManyToManyField): Many-to-Many relationship with Station through
                                    AssocRouteStation. Stations along this route, ordered by
                                    `elapsed_distance`.

    Meta:
        db_table (str): The name of the database table for this model (set to "Route").

    Usage Example:
        To create a new Route instance and associate it with a scenario:
        >>> scenario_instance = Scenario.objects.get(id=1)
        >>> departure_station_instance = Station.objects.get(id=2)
        >>> arrival_station_instance = Station.objects.get(id=3)
        >>> route_instance = Route(
        ...     geom=LineString([(lon1, lat1, ele1), (lon2, lat2, ele2)]),
        ...     distance=120.5,
        ...     name="Main Route",
        ...     name_short="MR",
        ...     scenario=scenario_instance,
        ...     line=line_instance,
        ...     headsign="Downtown",
        ...     departure_station=departure_station_instance,
        ...     arrival_station=arrival_station_instance,
        ... )
        >>> route_instance.save()
    """

    class Meta:
        db_table = "Route"
        # TODO: We should do a check here to make sure that if a geometry is provided, it's length
        # matches the distance field. In raw SQL: "ST_Length(geom) = distance". Not sure how to
        # do this in Django though.

    # Shape of the route with height data
    geom = models.LineStringField(dim=3, srid=4326, null=True)
    distance = models.FloatField(default=None, null=False)

    objects = FastUpdateManager()
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

    stations = models.ManyToManyField(Station, through="AssocRouteStation")
    """Stations along this route. Ordered by `elapsed_distance`."""

    vector_tiles = MVTManager(geo_col="geom", columns=["id", "geom", "name"])


class AssocRouteStation(models.Model):
    """
    This model is used to store the many-to-many relationship between Route and Station. It also contains metadata
    about the elapsed distance between the stations, which is also used to order the stations along the route on the
    `route` side of the relationship.
    """

    class Meta:
        db_table = "AssocRouteStation"
        ordering = ["elapsed_distance"]

    objects = FastUpdateManager()

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)

    route = models.ForeignKey(Route, on_delete=models.CASCADE)
    station = models.ForeignKey(Station, on_delete=models.CASCADE)

    elapsed_distance = models.FloatField(null=False)
    """The distance in m that the bus has traveled when it reached this stop."""

    location = models.PointField(dim=3, srid=4326, null=True)
    """An optional precise location of the this route's stop at the station. Use WGS84 coordinates (EPSG:4326)."""


class EnumTripType(models.TextChoices):
    EMPTY_TRIP = "EMPTY"
    PASSENGER_TRIP = "PASSENGER"


class Trip(models.Model):
    """
    Model representing a trip associated with a scenario.

    Attributes:
        scenario (Scenario): The scenario to which the trip is associated.
                             Foreign key to the Scenario model.
        route (Route): The route taken for the trip. Foreign key to the Route model.
        rotation (Rotation): The rotation associated with the trip.
                             Foreign key to the Rotation model.

        departure_time (DateTimeField): The departure time for the trip. Cannot be blank.
        arrival_time (DateTimeField): The arrival time for the trip. Cannot be blank.

        trip_type (str): The type of the trip. Choices defined by EnumTripType.
                    Defaults to EnumTripType.PASSENGER_TRIP.

        loaded_mass (float, optional): The mass that is loaded in [kg], i.e. mass of passengers.

    Properties:
        duration_in_seconds (float): Duration of the trip in seconds, with a minimum value
                                     of MINIMAL_TRIP_DURATION_S.
        speed (float): Speed in distance units per second,calculated using the
                       duration_in_seconds property.
        incline (float): Incline in z units per distance units, calculated using the route's
                         departure and arrival stations.

    Meta:
        db_table (str): The name of the database table for this model (set to "Trip").

    Usage Example:
        To create a new Trip instance and associate it with a scenario, route, and rotation:
        >>> scenario_instance = Scenario.objects.get(id=1)
        >>> route_instance = Route.objects.get(id=2)
        >>> rotation_instance = Rotation.objects.get(id=3)
        >>> trip_instance = Trip(
        ...     scenario=scenario_instance,
        ...     route=route_instance,
        ...     rotation=rotation_instance,
        ...     departure_time=datetime.datetime(2024, 1, 1, 8, 0, 0),
        ...     arrival_time=datetime.datetime(2024, 1, 1, 9, 0, 0),
        ...     trip_type=EnumTripType.PASSENGER_TRIP,
        ...     level_of_loading=0.8,
        ... )
        >>> trip_instance.save()
    """

    class Meta:
        db_table = "Trip"

    objects = FastUpdateManager()
    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    route = models.ForeignKey(Route, null=False, on_delete=models.CASCADE)

    rotation = models.ForeignKey(
        Rotation, on_delete=models.CASCADE
    )  # TODO do all ForeignKeys need cascade?

    departure_time = models.DateTimeField(blank=False)

    arrival_time = models.DateTimeField(blank=False)

    # Is the Trip empty, i.e., without passengers
    trip_type = models.CharField(
        max_length=9, choices=EnumTripType.choices, default=EnumTripType.PASSENGER_TRIP
    )

    loaded_mass = models.FloatField(default=None, null=True)

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
        return self.route.distance / self.duration_in_seconds

    @property
    def incline(self):
        """incline in z units per distance units

        Minimal value for distance is set to 1 to avoid division by 0."""
        return (self.route.arrival_station.geom.z - self.route.departure_station.geom.z) / max(
            self.route.distance, 1
        )


class StopTime(models.Model):
    """
    Model representing intermediate stops of trips, which are not described by the arrival or departure of the trip.

    Attributes:
        scenario (Scenario): The scenario to which the stop time is associated. Foreign key to the Scenario model.
        arrival_time (DateTimeField): The time when the trip arrives at this station. Cannot be null.
        dwell_duration (DurationField): The duration the trip stops at this station. Defaults to timedelta(seconds=0).
        trip (Trip): The trip associated with the stop time. Foreign key to the Trip model.
        station (Station): The station associated with the stop time. Foreign key to the Station model.

    Meta:
        db_table (str): The name of the database table for this model (set to "StopTime").

    Usage Example:
        To create a new StopTime instance and associate it with a scenario, trip, and station:
        >>> scenario_instance = Scenario.objects.get(id=1)
        >>> trip_instance = Trip.objects.get(id=2)
        >>> station_instance = Station.objects.get(id=3)
        >>> stop_time_instance = StopTime(
        ...     scenario=scenario_instance,
        ...     arrival_time=datetime.datetime(2024, 1, 1, 8, 15, 0),
        ...     dwell_duration=timedelta(minutes=10),
        ...     trip=trip_instance,
        ...     station=station_instance,
        ... )
        >>> stop_time_instance.save()
    """

    class Meta:
        db_table = "StopTime"

    """Intermediate stops of trips,
    which are not described by the arrival or departure of the trip"""
    objects = FastUpdateManager()
    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)

    # When does the trip arrive at this station
    arrival_time = models.DateTimeField()

    # How long does the trip stop at this station
    dwell_duration = models.DurationField(null=False, default=timedelta(seconds=0))

    trip = models.ForeignKey(Trip, on_delete=models.CASCADE)
    station = models.ForeignKey(Station, on_delete=models.CASCADE)


class DefaultScenario(models.Model):
    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    # Make sure only a single DefaultScenario exists
    _singleton = models.BooleanField(default=True, editable=False, unique=True)

    def save(self, *args, **kwargs):
        if not self._singleton:
            raise AttributeError("DefaultScenario._singleton must be True")
        if DefaultScenario.objects.all().count() > 0:
            raise AttributeError(
                "A DefaultScenario exists already. No other Default can be " "generated"
            )
        super().save(*args, **kwargs)


class EventType(models.TextChoices):
    """
    The EventType represents a certain type of event, which is used to define the type of an event.
    """

    DRIVING = "DRIVING"
    CHARGING_OPPORTUNITY = "CHARGING_OPPORTUNITY"
    CHARGING_DEPOT = "CHARGING_DEPOT"
    SERVICE = "SERVICE"
    STANDBY_DEPARTURE = "STANDBY_DEPARTURE"
    PRECONDITIONING = "PRECONDITIONING"


class Event(models.Model):
    """
    Model representing an event associated with a scenario.

    Attributes:
        scenario (Scenario): The scenario to which the event is associated. Foreign key to the Scenario model.
        vehicle_type (VehicleType): The type of vehicle associated with the event. Foreign key to the VehicleType model.
        vehicle (Vehicle, optional): The specific vehicle associated with the event. Defaults to None.

        station (Station, optional): The station associated with the event. Defaults to None.
        trip (Trip, optional): The trip associated with the event. Defaults to None.
        area (Area, optional): The area associated with the event. Defaults to None.

        subloc_no (int, optional): The sublocation number. Can be blank.
        time_start (DateTimeField): The start time of the event. Cannot be null.
        time_end (DateTimeField): The end time of the event. Cannot be null.

        soc_start (float): The state of charge at the start of the event.
        soc_end (float): The state of charge at the end of the event.

        event_type (str): The type of the event. Choices defined by EventType. Cannot be null,
                          defaults to None.
        description (str, optional): A description of the event. Can be blank.

        timeseries (JSONField): A JSON field containing event-specific time series data.
                                Defaults to an empty dictionary.
                               The dictionary must contain keys 'time' and 'soc', and their values
                               should be lists of the same length.

    Methods:
        save(self, *args, **kwargs): Override of the save method to ensure the consistency of
                                     event attributes.

    Meta:
        db_table (str): The name of the database table for this model (set to "Event").

    Usage Example:
        To create a new Event instance and associate it with a scenario, vehicle type, and specific vehicle:
        >>> scenario_instance = Scenario.objects.get(id=1)
        >>> vehicle_type_instance = VehicleType.objects.get(id=2)
        >>> vehicle_instance = Vehicle.objects.get(id=3)
        >>> event_instance = Event(
        ...     scenario=scenario_instance,
        ...     vehicle_type=vehicle_type_instance,
        ...     vehicle=vehicle_instance,
        ...     station=None,
        ...     trip=None,
        ...     area=None,
        ...     subloc_no=1,
        ...     time_start=datetime.datetime(2024, 1, 1, 8, 0, 0),
        ...     time_end=datetime.datetime(2024, 1, 1, 9, 0, 0),
        ...     soc_start=0.2,
        ...     soc_end=0.8,
        ...     event_type=EventType.CHARGING.value,
        ...     description="Charging Event",
        ...     timeseries={"time": [0, 1, 2], "soc": [0.2, 0.5, 0.8]},
        ... )
        >>> event_instance.save()
    """

    class Meta:
        db_table = "Event"

    objects = FastUpdateManager()
    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    vehicle_type = models.ForeignKey(VehicleType, null=False, on_delete=models.CASCADE)
    vehicle = models.ForeignKey(Vehicle, null=True, on_delete=models.CASCADE)

    #
    station = models.ForeignKey(Station, null=True, on_delete=models.CASCADE)
    trip = models.ForeignKey(Trip, null=True, on_delete=models.CASCADE)
    area = models.ForeignKey("Area", null=True, on_delete=models.CASCADE)

    subloc_no = models.IntegerField(null=True, blank=True)
    time_start = models.DateTimeField()
    time_end = models.DateTimeField()

    soc_start = models.FloatField()
    soc_end = models.FloatField()

    event_type = models.CharField(
        max_length=20, choices=EventType.choices, null=False, default=None
    )
    description = models.TextField(null=True, blank=True)

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


class Depot(models.Model):
    """
    The Depot represents a place where vehicles not engaged in a schedule are parked,
    processed and dispatched.
    """

    class Meta:
        db_table = "Depot"

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    name = models.TextField(null=False, blank=False)
    name_short = models.TextField(null=True, blank=True)
    bounding_box = models.PolygonField(dim=2, srid=4326, null=True, default=None)
    station = models.ForeignKey(Station, null=False, on_delete=models.CASCADE)  # Added in schema v3

    default_plan = models.OneToOneField("Plan", null=False, on_delete=models.CASCADE)


class Plan(models.Model):
    """
    The Plan represents a certain order of processes, which are executed on vehicles in a depot.
    """

    class Meta:
        db_table = "Plan"

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    name = models.TextField(null=False, blank=False)
    processes = models.ManyToManyField("Process", through="AssocPlanProcess")


class Process(models.Model):
    """
    The Process represents a certain action, which is executed on vehicles in a depot.
    """

    class Meta:
        db_table = "Process"

    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE)
    name = models.TextField(null=False)
    name_short = models.TextField(null=True)
    duration = models.DurationField(null=True)
    electric_power = models.FloatField(null=True)
    dispatchable = models.BooleanField(null=False)
    availability = models.JSONField(default=dict, null=True)
    plans = models.ManyToManyField(Plan, through="AssocPlanProcess")


class AreaType(models.TextChoices):
    """
    The AreaType represents a certain type of area, which is used to define the location of a process.
    """

    DIRECT_ONESIDE = "DIRECT_ONESIDE"
    DIRECT_TWOSIDE = "DIRECT_TWOSIDE"
    LINEAR = "LINE"


class Area(models.Model):
    """
    The Area represents a certain location, which is used to define the location of a process.
    """

    class Meta:
        db_table = "Area"

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    depot = models.ForeignKey(Depot, null=False, on_delete=models.CASCADE)
    vehicle_type = models.ForeignKey(VehicleType, null=True, on_delete=models.CASCADE)
    name = models.TextField(null=True)
    name_short = models.TextField(null=True)
    area_type = models.CharField(max_length=14, choices=AreaType.choices, null=True, default=None)
    bounding_box = models.PolygonField(dim=2, srid=4326, null=True, default=None)
    row_count = models.IntegerField(null=True, default=None)
    capacity = models.IntegerField(null=False)
    processes = models.ManyToManyField(Process, through="AssocAreaProcess")


class AssocPlanProcess(models.Model):
    """
    This model is used to store the many-to-many relationship between Plan and Process.
    """

    class Meta:
        db_table = "AssocPlanProcess"

    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE)
    process = models.ForeignKey(Process, on_delete=models.CASCADE)

    ordinal = models.IntegerField(null=False)


class AssocAreaProcess(models.Model):
    """
    This model is used to store the many-to-many relationship between Area and Process.
    """

    class Meta:
        db_table = "AssocAreaProcess"

    area = models.ForeignKey(Area, on_delete=models.CASCADE)
    process = models.ForeignKey(Process, on_delete=models.CASCADE)


class SimulationRange(models.Model):
    # Mutation Scenario
    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    start = models.DateTimeField(null=True)
    end = models.DateTimeField(null=True)
    temperature_average = models.FloatField(
        blank=True,
        default=-10,
        null=True,
        validators=[MinValueValidator(-20), MaxValueValidator(40)],
    )
    temperature_extreme = models.FloatField(
        blank=True,
        default=-10,
        null=True,
        validators=[MinValueValidator(-20), MaxValueValidator(40)],
    )


class DepotSelection(models.Model):
    # Mutation Scenario
    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    depots = models.ManyToManyField(Station)


class ElectrificationOptions(models.Model):
    # Mutation Scenario
    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    gc_power_opps = models.PositiveIntegerField(
        default=5000, null=False, validators=[MinValueValidator(1), MaxValueValidator(1000000)]
    )

    cs_power_opps = models.PositiveIntegerField(
        default=150, null=False, validators=[MinValueValidator(1), MaxValueValidator(1000000)]
    )
    amount_charging_places = models.PositiveIntegerField(
        default=2, null=False, validators=[MinValueValidator(1), MaxValueValidator(9999)]
    )
    station_optimization = models.BooleanField(null=False)
    electrified_stations = models.ManyToManyField(Station)


# Models for forms which do not mutate the scenario while in the wizard
class VehicleTypeSelection(models.Model):
    default_vehicle_type = models.ForeignKey(
        VehicleType, related_name="formdefaultvehicletype", null=True, on_delete=models.CASCADE
    )
    vehicle_type = models.ForeignKey(
        VehicleType, related_name="formvehicletype", null=False, on_delete=models.CASCADE
    )


class VehicleTypeMutation(models.Model):
    scenario = models.ForeignKey(Scenario, null=True, on_delete=models.CASCADE)
    original_vehicle_type = models.ForeignKey(
        VehicleType, related_name="originalvehicletype", null=True, on_delete=models.CASCADE
    )
    mutated_vehicle_type = models.ForeignKey(
        VehicleType, related_name="mutatedvehicletype", null=True, on_delete=models.CASCADE
    )


class DepotMutation(models.Model):
    scenario = models.ForeignKey(Scenario, null=True, on_delete=models.CASCADE)
    original_depot = models.ForeignKey(
        Depot, related_name="originaldepot", null=True, on_delete=models.CASCADE
    )
    mutated_original_depot = models.ForeignKey(
        Depot, related_name="mutateddepot", null=True, on_delete=models.CASCADE
    )


class StationMutation(models.Model):
    scenario = models.ForeignKey(Scenario, null=True, on_delete=models.CASCADE)
    original_station = models.ForeignKey(
        Station, related_name="originalstation", null=True, on_delete=models.CASCADE
    )
    mutated_original_station = models.ForeignKey(
        Station, related_name="mutatedstation", null=True, on_delete=models.CASCADE
    )


class EnumCalculationModes(models.TextChoices):
    AUTOMATIC = "automatic"
    CONSTANT_POWER = "constant_power"
    MANUAL = "manual"


class ScenarioWizardOptions(models.Model):
    scenario = models.ForeignKey(Scenario, null=False, on_delete=models.CASCADE)
    station_calculation_mode = models.CharField(
        max_length=20, choices=EnumCalculationModes.choices, null=True, default=None
    )
    tco_calculation_mode = models.CharField(
        max_length=20, choices=EnumCalculationModes.choices, null=True, default=None
    )

    lca_calculation_mode = models.CharField(
        max_length=20, choices=EnumCalculationModes.choices, null=True, default=None
    )
