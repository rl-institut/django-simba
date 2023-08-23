import requests

from django.db import models

# Create your models here.
from django.contrib.gis.db import models
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from ebus_map.managers import MVTManager, LabelMVTManager

from ebustoolbox.models import *


def generalizedLayerLoader(wfs_url, type_names, sbbox, version='2.0.0', retries=3, timeout=30):
    """
    Retrieves data from a WFS service using the specified URL and parameters.

    Parameters:
    wfs_url (str): The URL of the WFS service.
    type_names (str): The name of the layer to query.
    version (str): The version of the WFS protocol to use (default is '2.0.0').
    retries (int): The number of times to retry the request if it fails (default is 3).
    timeout (int): The number of seconds to wait for a response before timing out (default is 30).

    Returns:
            Tuple, signifying the success of the request and the returned Geodataframe
    """
    gdf = gpd.GeoDataFrame()
    params = {
        "service": "WFS",
        "version": version,
        "request": "GetFeature",
        "typeNames": type_names,
        "bbox": f'{sbbox[0]},{sbbox[1]},{sbbox[2]},{sbbox[3]},urn:ogc:def:crs:EPSG:25833'

    }

    wfs_request_url = requests.Request('GET', wfs_url, params=params).prepare().url

    for i in range(retries):
        try:

            response = requests.get(wfs_request_url, timeout=timeout)
            if response.status_code == 200:
                try:
                    gdf = gpd.read_file(wfs_request_url)
                    gdf.crs = 25833
                    return (1, gdf)
                except:
                    return (0, gdf)
        except requests.exceptions.RequestException as e:
            print("Error: Request failed with exception", e)
            return (0, gdf)

    print("Error: Request failed after", retries, "retries")
    return (0, gdf)



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

    from django.db.models.functions import Length
    from django.db.models import Value

#    annotations = {"center": models.functions.Centroid("geom"),
#                   "lat": X("center", output_field=models.DecimalField()),
#                   "lon": Y("center", output_field=models.DecimalField())}



class Flurstueck(models.Model):
    geom = models.MultiPolygonField(srid=4326)
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





