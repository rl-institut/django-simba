import requests

from django.db import models
from django.db.models import Sum
from ebustoolbox.models import *

from django.contrib.gis.db import models
from django.utils.translation import gettext_lazy as _
from ebus_map.managers import MVTManager, LabelMVTManager
from ebustoolbox.models import Scenario as EbustoolboxScenario

class BusOutline(models.Model):
    geom = models.MultiPolygonField(srid=4326)
    name = models.CharField(max_length=50)
    scenario = models.ForeignKey(EbustoolboxScenario, null=False, on_delete=models.CASCADE)
    quality = models.IntegerField(default=0)

    objects = models.Manager()
    layer = "busstop"
    vector_tiles = MVTManager(columns=["id", "name", "quality"])
    # label_tiles = LabelMVTManager(geo_col="geom_label", columns=["id", "name"])
    mapping = {
        "geom": "MultiPolygon",
        "name": "name",
        "quality": "quality",
    }


class Flurstueck(models.Model):
    geom = models.MultiPolygonField(srid=4326)
    name = models.CharField(max_length=50)
    scenario_ID = models.CharField(max_length=50)

    objects = models.Manager()
    layer = "busstop"
    vector_tiles = MVTManager(columns=["id", "name"])
    mapping = {
        "geom": "MultiPolygon",
        "name": "name",
    }


class Tree(models.Model):
    geom = models.PointField(srid=4326)
    name = models.CharField(max_length=50)
    scenario_ID = models.CharField(max_length=50)

    objects = models.Manager()
    layer = "busstop"
    vector_tiles = MVTManager(columns=["id", "name"])
    # label_tiles = LabelMVTManager(geo_col="geom_label", columns=["id", "name"])
    mapping = {
        "geom": "MultiPolygon",
        "name": "name",
    }


class Station(models.Model):
    geom = models.PointField(srid=4326)
    name = models.CharField(max_length=50)
    # Reference to ebustoolbox.Scenario with a custom related_name
    scenario = models.ForeignKey(
        EbustoolboxScenario,
        on_delete=models.CASCADE,
        related_name='HPCtool_Station',
        db_column='scenario_ID'
    )
    charge_unit = models.CharField(max_length=10, default="kW")
    station_unit = models.CharField(max_length=10, default="kW")
    voltage_level = models.CharField(max_length=10, default="MV")
    charge_power = models.FloatField(default=0.0)
    station_power = models.FloatField(default=0.0)

    busses = models.ManyToManyField(BusOutline)
    flurstück = models.ManyToManyField(Flurstueck)
    trees = models.ManyToManyField(Tree)

    objects = models.Manager()
    layer = "busstop"
    vector_tiles = MVTManager(columns=["id", "name"])

class Cyclepath(models.Model):
    geom = models.MultiPolygonField(srid=4326)
    name = models.CharField(max_length=50)
    scenario_ID = models.CharField(max_length=50)

    objects = models.Manager()
    layer = "busstop"
    vector_tiles = MVTManager(columns=["id", "name"])


class ResidentialArea(models.Model):
    geom = models.MultiPolygonField(srid=4326)
    name = models.CharField(max_length=50)
    scenario_ID = models.CharField(max_length=50)

    objects = models.Manager()
    layer = "busstop"
    vector_tiles = MVTManager(columns=["id", "name"])
    mapping = {
        "geom": "MultiPolygon",
        "name": "name",
    }


class Criterion(models.Model):
    name = models.CharField(max_length=50)
    scenario_ID = models.CharField(max_length=50)

    geom_type = models.CharField(max_length=50)
    link = models.CharField(max_length=150)
    layer_name = models.CharField(max_length=50)

    dist_red = models.FloatField()
    dist_green = models.FloatField()


class Settings(models.Model):
    name = models.CharField(max_length=50)
    scenario_ID = models.CharField(max_length=50)

    bus_length = models.FloatField()
    park_distance = models.FloatField()
    max_curvature = models.FloatField()

    criteria = models.ManyToManyField(Criterion)


