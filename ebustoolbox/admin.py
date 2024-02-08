from django.contrib import admin

from .models import Scenario, UserGroup


class UserGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "users_count", "scenarios_count")
    list_filter = ("users", "scenarios")

    def users_count(self, obj):
        return obj.users.all().count()

    def scenarios_count(self, obj):
        return obj.scenarios.all().count()


admin.site.register(Scenario)
admin.site.register(UserGroup, UserGroupAdmin)
