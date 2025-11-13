from django.contrib import admin

from .models import (
    Scenario,
    UserGroup,
    EnumScenarioType,
    UploadedFile,
    BatteryType,
    VehicleType,
    ChargingPointType,
    VehicleClass,
    Consumption,
    Vehicle,
    Rotation,
    Temperatures,
    Station,
    Line,
    Route,
    AssocRouteStation,
    Trip,
    StopTime,
    DefaultScenario,
    Event,
    Depot,
    Plan,
    Process,
    Area,
    AssocPlanProcess,
    AssocAreaProcess,
    SimulationRange,
    DepotSelection,
    VehicleTypeSelection,
    VehicleTypeMutation,
    StationMutation,
    DepotConfigurationWish,
    AreaInformation,
    Notification,
)


class DataScenarioFilter(admin.SimpleListFilter):
    title = "Data Scenarios"
    parameter_name = "scenario_type"

    def lookups(self, request, model_admin):
        return [(EnumScenarioType.PUBLIC_DATA, "Public Data Scenarios"), ("all", "All Scenarios")]

    def queryset(self, request, queryset):
        if self.value() == "all":
            return queryset
        return queryset.filter(scenario_type=EnumScenarioType.PUBLIC_DATA)


class ScenarioAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario_type", "created", "finished", "task_id", "manager")
    list_filter = [
        DataScenarioFilter,
        ("manager", admin.RelatedOnlyFieldListFilter),
    ]


admin.site.register(Scenario, ScenarioAdmin)


class VehicleTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario")


admin.site.register(VehicleType, VehicleTypeAdmin)


class UserGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "users_count", "scenarios_count")
    list_filter = ("users", "scenarios")

    def users_count(self, obj):
        return obj.users.all().count()

    def scenarios_count(self, obj):
        return obj.scenarios.all().count()


admin.site.register(UserGroup, UserGroupAdmin)


@admin.register(UploadedFile)
class UploadedFileAdmin(admin.ModelAdmin):
    list_display = ("file", "scenario")


@admin.register(BatteryType)
class BatteryTypeAdmin(admin.ModelAdmin):
    list_display = ("scenario",)


@admin.register(ChargingPointType)
class ChargingPointTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario")


@admin.register(VehicleClass)
class VehicleClassAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario")


@admin.register(Consumption)
class ConsumptionAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario")


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario")


@admin.register(Rotation)
class RotationAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario")


@admin.register(Temperatures)
class TemperaturesAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario")


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario")


@admin.register(Line)
class LineAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario")


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario")


@admin.register(AssocRouteStation)
class AssocRouteStationAdmin(admin.ModelAdmin):
    list_display = ("route", "scenario")


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ("rotation", "scenario")


@admin.register(StopTime)
class StopTimeAdmin(admin.ModelAdmin):
    list_display = ("station", "scenario")


@admin.register(DefaultScenario)
class DefaultScenarioAdmin(admin.ModelAdmin):
    list_display = ("scenario",)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("vehicle", "scenario")


@admin.register(Depot)
class DepotAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario")


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario")


@admin.register(Process)
class ProcessAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario")


@admin.register(Area)
class AreaAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario")


@admin.register(AssocPlanProcess)
class AssocPlanProcessAdmin(admin.ModelAdmin):
    list_display = ("plan", "scenario")


@admin.register(AssocAreaProcess)
class AssocAreaProcessAdmin(admin.ModelAdmin):
    list_display = ("area", "process")


@admin.register(SimulationRange)
class SimulationRangeAdmin(admin.ModelAdmin):
    list_display = ("scenario",)


@admin.register(DepotSelection)
class DepotSelectionAdmin(admin.ModelAdmin):
    list_display = ("scenario",)


@admin.register(VehicleTypeSelection)
class VehicleTypeSelectionAdmin(admin.ModelAdmin):
    list_display = ("vehicle_type",)


@admin.register(VehicleTypeMutation)
class VehicleTypeMutationAdmin(admin.ModelAdmin):
    list_display = ("original_vehicle_type", "scenario")


@admin.register(StationMutation)
class StationMutationAdmin(admin.ModelAdmin):
    list_display = ("original_station", "scenario")


@admin.register(DepotConfigurationWish)
class DepotConfigurationWishAdmin(admin.ModelAdmin):
    list_display = ("station", "scenario")


@admin.register(AreaInformation)
class AreaInformationAdmin(admin.ModelAdmin):
    list_display = ("depot_configuration_wish", "scenario")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("notification_type", "scenario")
