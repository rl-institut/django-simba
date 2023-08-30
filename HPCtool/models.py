import requests

from django.db import models
from django.db.models import Sum
from ebustoolbox.models import *

from django.contrib.gis.db import models
from django.utils.translation import gettext_lazy as _
from ebus_map.managers import MVTManager, LabelMVTManager

class BusOutline(models.Model):
    geom = models.MultiPolygonField(srid=4326)
    name = models.CharField(max_length=50)
    scenario = models.CharField(max_length=50)
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
    scenario = models.CharField(max_length=50)

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
    scenario = models.CharField(max_length=50)

    objects = models.Manager()
    layer = "busstop"
    vector_tiles = MVTManager(columns=["id", "name"])
    # label_tiles = LabelMVTManager(geo_col="geom_label", columns=["id", "name"])
    mapping = {
        "geom": "MultiPolygon",
        "name": "name",
    }