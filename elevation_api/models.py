from django.contrib.gis.db import models


class Elevation(models.Model):
    name = models.CharField(max_length=100, null=True)
    raster = models.RasterField(null=True)
