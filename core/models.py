# Create your models here.
from django.contrib.gis.db import models

from ebustoolbox.models import Scenario


class Progress(models.Model):
    task_id = models.UUIDField(null=False, unique=True)
    scenario = models.ForeignKey(Scenario, null=True, on_delete=models.CASCADE)
    status = models.CharField(max_length=100)
    total_work = models.IntegerField(null=True)
    current_work = models.IntegerField(default=0)
    success = models.BooleanField(default=False)
    running = models.BooleanField(default=True)

    errors = models.JSONField([], default=list, null=True)

    def get_progress(self):
        try:
            return self.current_work / self.total_work * 100
        except ZeroDivisionError:
            return 0
