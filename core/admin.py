# Register your models here.
from django.contrib import admin

from .models import Progress


@admin.register(Progress)
class ProgressAdmin(admin.ModelAdmin):
    list_display = ("progress_type", "scenario")
