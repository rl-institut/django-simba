from django.contrib import admin

from .models import Scenario, UserGroup, VehicleType


class ScenarioAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario_type", "created", "finished", "task_id", "manager")


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
