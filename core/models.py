# Create your models here.
from django.contrib.gis.db import models

from ebustoolbox.models import Scenario


class Progress(models.Model):
    task_id = models.UUIDField(null=False, unique=True)
    scenario = models.ForeignKey(Scenario, null=True, on_delete=models.CASCADE)
    status = models.CharField(max_length=100)
    total_work = models.IntegerField(default=1, null=True)
    current_work = models.IntegerField(default=0)
    success = models.BooleanField(default=False)
    running = models.BooleanField(default=True)

    errors = models.JSONField([], default=list, null=True)

    def get_progress(self):
        """Return a progress, which should be between 0 and 100."""
        try:
            return self.current_work / self.total_work * 100
        except ZeroDivisionError:
            return 0

    def set_success(self):
        self.status = "Finished"
        self.current_work = self.total_work
        self.success = True
        self.running = False
        self.save()

    def set_failed(self):
        self.status = "Failed"
        self.success = False
        self.running = False
        self.save()

    def reset(self):
        self.status = "Started"
        self.current_work = 0
        self.total_work = 1
        self.success = False
        self.running = True
        self.errors = []
        self.save()
