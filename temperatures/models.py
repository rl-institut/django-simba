from django.contrib.gis.db import models


# Create your models here.
class WeatherStation(models.Model):
    name = models.TextField(db_index=True, null=False)
    dwd_id = models.IntegerField(unique=True, db_index=True)
    geom = models.PointField(dim=3, srid=4326, null=False)


class WeatherData(models.Model):
    """Weather data
    temperature in ˚C
    """

    weatherstation = models.ForeignKey(WeatherStation, on_delete=models.CASCADE, db_index=True)
    time = models.DateTimeField()
    air_temperature = models.FloatField(null=True)
