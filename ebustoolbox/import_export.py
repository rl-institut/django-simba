"""
Module for importing and exporting WeBus Scenarios.
Uses a Visitor to visit all relevant Scenario objects to export the Scenario to json"""

from django.core.serializers import serialize  # noqa
from abc import ABCMeta, abstractmethod
from ebustoolbox.models import (
    Scenario,
    BatteryType,
    VehicleType,
    VehicleClass,
    Consumption,
    Vehicle,
    Rotation,
    Temperatures,
    Station,
    Line,
    Route,
    Trip,
    StopTime,
    Event,
    Depot,
    Plan,
    Process,
    Area,
)


class ScenarioElementVisitor(metaclass=ABCMeta):
    # Scenario
    @abstractmethod
    def visitScenario(self, element: Scenario):
        raise NotImplementedError()

    # BatteryType
    @abstractmethod
    def visistBatteryType(self, element: BatteryType):
        raise NotImplementedError()

    # VehicleType
    @abstractmethod
    def visistVehicleType(self, element: VehicleType):
        raise NotImplementedError()

    # VehicleClass
    @abstractmethod
    def visistVehicleClass(self, element: VehicleClass):
        raise NotImplementedError()

    # Consumption
    @abstractmethod
    def visistConsumption(self, element: Consumption):
        raise NotImplementedError()

    # Vehicle
    @abstractmethod
    def visistVehicle(self, element: Vehicle):
        raise NotImplementedError()

    # Rotation
    @abstractmethod
    def visistRotation(self, element: Rotation):
        raise NotImplementedError()

    # Temperatures
    @abstractmethod
    def visistTemperatures(self, element: Temperatures):
        raise NotImplementedError()

    # Station
    @abstractmethod
    def visistStation(self, element: Station):
        raise NotImplementedError()

    # Line
    @abstractmethod
    def visistLine(self, element: Line):
        raise NotImplementedError()

    # Route
    @abstractmethod
    def visistRoute(self, element: Route):
        raise NotImplementedError()

    # Trip
    @abstractmethod
    def visistTrip(self, element: Trip):
        raise NotImplementedError()

    # StopTime
    @abstractmethod
    def visistStopTime(self, element: StopTime):
        raise NotImplementedError()

    # Event
    @abstractmethod
    def visistEvent(self, element: Event):
        raise NotImplementedError()

    # Depot
    @abstractmethod
    def visistDepot(self, element: Depot):
        raise NotImplementedError()

    # Plan
    @abstractmethod
    def visistPlan(self, element: Plan):
        raise NotImplementedError()

    # Process
    @abstractmethod
    def visistProcess(self, element: Process):
        raise NotImplementedError()

    # Area
    @abstractmethod
    def visistArea(self, element: Area):
        raise NotImplementedError()


def visit_all_scenario_objects(visitor: ScenarioElementVisitor, scenario: Scenario):
    visitor.visitScenario(scenario)
    # All relevant models have a foreign key to this scenario
    models = [
        Rotation,
        VehicleType,
        BatteryType,
        VehicleClass,
        Vehicle,
        Consumption,
        Temperatures,
        Station,
        Depot,
        Trip,
        Line,
        Route,
        StopTime,
        Event,
        Plan,
        Process,
        Area,
    ]
    for model in models:
        visit_method = getattr(visitor, "visit" + model.__qualname__)
        objects = model.objects.filter(scenario=scenario)
        for object in objects:
            visit_method(object)


class ScenarioJSONExporter:
    def __init__(self):
        # Stores all json serialized
        self.object_data: dict[str, list[object]] = dict()

    def visitScenario(self, element: Scenario):
        self.object_data["Scenario"] = [element]

    def visitBatteryType(self, element: BatteryType):
        raise NotImplementedError()

    def visitVehicleType(self, element: VehicleType):
        raise NotImplementedError()

    def visitVehicleClass(self, element: VehicleClass):
        raise NotImplementedError()

    def visitConsumption(self, element: Consumption):
        raise NotImplementedError()

    def visitVehicle(self, element: Vehicle):
        raise NotImplementedError()

    def visitRotation(self, element: Rotation):
        raise NotImplementedError()

    def visitTemperatures(self, element: Temperatures):
        raise NotImplementedError()

    def visitStation(self, element: Station):
        raise NotImplementedError()

    def visitLine(self, element: Line):
        raise NotImplementedError()

    def visitRoute(self, element: Route):
        raise NotImplementedError()

    def visitTrip(self, element: Trip):
        raise NotImplementedError()

    def visitStopTime(self, element: StopTime):
        raise NotImplementedError()

    def visitEvent(self, element: Event):
        raise NotImplementedError()

    def visitDepot(self, element: Depot):
        raise NotImplementedError()

    def visitPlan(self, element: Plan):
        raise NotImplementedError()

    def visitProcess(self, element: Process):
        raise NotImplementedError()

    def visitArea(self, element: Area):
        raise NotImplementedError()
