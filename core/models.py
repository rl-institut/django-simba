import datetime
import pytz

# Create your models here.
from django.contrib.gis.db import models
from django.db.models.functions import Now
from django.utils.translation import gettext as _
from ebustoolbox.models import Scenario


class EnumProgress(models.TextChoices):
    INIT_SCHEDULE = "INIT_SCHEDULE"
    RUNNING_SIMULATION = "RUNNING_SIMULATION"


class Progress(models.Model):
    task_id = models.UUIDField(null=False, unique=True)
    progress_type = models.CharField(
        max_length=20, choices=EnumProgress.choices, null=True, default=None
    )
    created = models.DateTimeField(null=True, auto_now_add=True, db_default=Now())

    scenario = models.ForeignKey(Scenario, null=True, on_delete=models.CASCADE)
    status = models.CharField(max_length=100)
    total_work = models.IntegerField(default=1, null=True)
    current_work = models.IntegerField(default=0)
    success = models.BooleanField(default=False)
    running = models.BooleanField(default=True)

    errors = models.JSONField(default=list, null=True)

    def estimate_duration(self):
        """Return the number of minutes estimated to finish based on
        linear extrapolation of the current and total work"""
        if self.current_work == 0:
            return None
        passed_duration_minutes = (
            datetime.datetime.now(pytz.UTC) - self.created
        ).total_seconds() / 60
        # Upper bound for estimation is first guess of duration when no progress was
        speed = self.current_work / passed_duration_minutes
        further_duration_minutes = (self.total_work - self.current_work) / speed
        return further_duration_minutes

    def get_progress(self):
        """Return a progress, which should be between 0 and 100."""
        if self.success:
            return 100
        try:
            return min(max(0, self.current_work / self.total_work * 100), 100)
        except ZeroDivisionError:
            return 0

    def set_success(self):
        self.status = _("Fertig")
        self.current_work = self.total_work
        self.success = True
        self.running = False
        self.save()

    def set_failed(self):
        self.status = "Fehlgeschlagen"
        self.success = False
        self.running = False
        self.save()

    def reset(self):
        self.status = "Gestartet"
        self.current_work = 0
        self.total_work = 1
        self.success = False
        self.created = Now()
        self.running = True
        self.errors = []
        self.save()
