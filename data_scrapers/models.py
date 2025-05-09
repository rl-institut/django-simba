import logging
from django.contrib.gis.db import models

logger = logging.getLogger("custom")


# https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL#By_element_id
class AdminArea(models.Model):
    """
    Model for hierarchical structuring of AdminAreas.

    Attributes:
        name (str): Name of the AdminArea
        admin_level (int): Administrative level of the AdminArea
        osm_id (int): OpenStreetMap identifier
        updated_at (Datetime): Last time this was updated
        upper_admin_area (AdminArea|None): AdminArea with the highest admin_level
        the current AdminArea is contained in.
    """

    name = models.CharField(max_length=100)
    admin_level = models.IntegerField(default=4)
    osm_id = models.BigIntegerField(unique=True)
    updated_at = models.DateTimeField(null=True)
    upper_admin_area = models.ForeignKey("self", on_delete=models.CASCADE, null=True)


class BusStation(models.Model):
    """
    Model for spatial resolving of BusStations

    Attributes:
        name (str): Name of BusStation
        osm_id (int): OpenStreetMap identifier
        geom (django.contrib.gis.geos.Point|None)): Geolocation of BusStation in SRID 4326
        admin_area (AdminArea): AdminArea with the highest admin_level the BusStation is contained in.
    """

    name = models.CharField(max_length=100)
    osm_id = models.BigIntegerField(unique=True)
    geom = models.PointField(dim=3, srid=4326, null=True)
    admin_area = models.ForeignKey(AdminArea, on_delete=models.CASCADE)
