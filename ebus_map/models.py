"""Only general data which is applicable for 'every' app should
be part of models."""
from django.contrib.gis.db import models
from django.utils.translation import gettext_lazy as _

from .managers import LabelMVTManager, MVTManager, X, Y

import ebustoolbox.models



class Station(ebustoolbox.models.Station):
    # prior attributes, used for map (?)
    objects = models.Manager()
    from django.db.models.functions import Length

    # Make sure all annotations are part of the columns below, if the data is supposed to be
    # delivered to the map
    annotations = {
        "center": models.functions.Centroid("geom"),
        "lat": X("center", output_field=models.DecimalField()),
        "lon": Y("center", output_field=models.DecimalField()),
        "title_length": Length("name")
    }

    vector_tiles = MVTManager(
        geo_col="geom", columns=["id", "geom", "name", "lat", "lon", "title_length"]
    )

    layer = "busstop"
    mapping = {
        "id": "id",
        "geom": "POINT",
        "name": "name",
        "geom_label": "geom_label",
    }

    @classmethod
    def get_popup_data(cls, id):
        obj = cls.objects.get(id=id)
        data = {}
        data["title"] = obj.name
        data["lat"] = obj.geom.x
        data["lon"] = obj.geom.y
        return data



class MyExampleMultiPolygon(models.Model):
    geom = models.MultiPolygonField(srid=4326)
    name = models.CharField(max_length=50)

    objects = models.Manager()
    layer = "busstop"
    vector_tiles = MVTManager(columns=["id", "name"])
    # label_tiles = LabelMVTManager(geo_col="geom_label", columns=["id", "name"])
    mapping = {
        "geom": "MultiPolygon",
        "name": "name",
    }


class MyExampleLine(models.Model):
    geom = models.LineStringField(srid=4326)
    name = models.CharField(max_length=50)

    objects = models.Manager()
    # vector_tiles = StaticMVTManager(
    #     geo_col="geom", columns=["id", "name"]
    # )
    # data_file = "bnetza_mastr_wind_agg_region"
    layer = "lines"
    vector_tiles = MVTManager(columns=["id", "name", "bbox"])
    label_tiles = LabelMVTManager(geo_col="geom_label", columns=["id", "name"])
    #
    mapping = {
        "geom": "Line",
        "name": "name",
    }

    class Meta:
        verbose_name = _("My Line")
        verbose_name_plural = _("My Lines")

    # def __str__(self):
    #     return self.name


class MyExamplePoint(models.Model):
    geom = models.PointField(srid=4326)
    # name = "One of my points"

    objects = models.Manager()
    vector_tiles = MVTManager(
        geo_col="geom", columns=["id"]
    )

    # data_file = "bnetza_mastr_wind_agg_region"
    layer = "wind"
    mapping = {
        "geom": "POINT",
        "name": "name",
    }

    class Meta:
        verbose_name = _("My Point")
        verbose_name_plural = _("My Points")

    # def __str__(self):
    #     return self.name




#
# class LayerFilterType(Enum):
#     Range = 0
#     Dropdown = 1
#
#
# @dataclass
# class LayerFilter:
#     name: str
#     type: LayerFilterType = LayerFilterType.Range  # noqa: A003
#
#
# # REGIONS
#



# from django.contrib.gis.geos import LineString
# line = LineString((0, 0), (0, 50), (50, 50), (50, 0), (0, 0))
# MyExampleLine(geom=line, name="My first line")
# MyExampleLine.objects.create(geom=LineString((0, 0), (0, 50), (50, 50), (50, 0), (0, 0), srid=4326))
#
#
a  = 2
# if len(MyExamplePoint.objects.all()) <= 100:
#     MyExamplePoint.objects.create(geom=GEOSGeometry("POINT(5 55)"))
#     MyExamplePoint.objects.create(geom=GEOSGeometry("POINT(8 50)"))
#     MyExamplePoint.objects.create(geom=GEOSGeometry("POINT(9 52)"))
#     print("foo")
# #
# print("foo")



from django.contrib.gis.geos import MultiPolygon, Polygon
p1 = Polygon(((0, 0), (0, 50), (50, 50), (0, 0)))
p2 = Polygon(((1, 1), (1, 2), (2, 2), (1, 1)))
mp = MultiPolygon(p1, p2)
#MyExampleMultiPolygon(geom=mp, name="My first Polygon")
# # region = Region.objects.create(layer_type="municipality")
#MyExampleMultiPolygon.objects.create(geom=mp, name="agfjdgsf")
# #
# # #
#