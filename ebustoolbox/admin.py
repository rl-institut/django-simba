from django.contrib import admin

from .models import Scenario, UserGroup, VehicleType, EnumScenarioType


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
