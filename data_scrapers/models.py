import logging

from django.contrib.gis.db import models

logger = logging.getLogger("custom")


# https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL#By_element_id
class AdminArea(models.Model):
    name = models.CharField(max_length=100)
    admin_level = models.IntegerField(default=4)
    osm_id = models.BigIntegerField(unique=True)
    updated_at = models.DateTimeField(null=True)
    upper_admin_area = models.ForeignKey("self", on_delete=models.CASCADE, null=True)


class BusStation(models.Model):
    name = models.CharField(max_length=100)
    osm_id = models.BigIntegerField(unique=True)
    geom = models.PointField(dim=3, srid=4326, null=True)
    admin_area = models.ForeignKey(AdminArea, on_delete=models.CASCADE)
