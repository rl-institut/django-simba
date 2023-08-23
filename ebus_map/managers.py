from django.contrib.gis.db import models
from django.contrib.gis.db.models.functions import Transform
from django.contrib.gis.geos import Polygon
from django.core.exceptions import FieldError
from django.db import connection
from rest_framework.serializers import ValidationError
from rest_framework_gis.tilenames import tile_edges


# pylint: disable=W0223
class AsMVTGeom(models.functions.GeomOutputGeoFunc):
    function = "ST_AsMVTGeom"
    geom_param_pos = (0, 1)


# pylint: disable=W0223
class X(models.functions.Func):
    function = "ST_X"


# pylint: disable=W0223
class Y(models.functions.Func):
    function = "ST_Y"


class MVTManager(models.Manager):
    def __init__(self, *args, geo_col="geom", columns=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.geo_col = geo_col
        self.columns = columns

    def get_queryset(self):
        query = super().get_queryset()
        try:
            annotations = self.model.annotations
        except:
            annotations = {}

        for key, func in annotations.items():
            query = query.annotate(**{key: func})

        return query

    def get_mvt_query(self, x, y, z, filters=None):
        filters = filters or {}
        #TODO: Hier liste mit Busstops, polygons
        return self._build_mvt_query(x, y, z, filters)

    def get_columns(self):
        return self.columns or self._get_non_geom_columns()

    # pylint: disable=W0613,R0913
    def _filter_query(self, query, x, y, z, filters):
        # ToDo Change
        try:
<<<<<<< HEAD
            filters = {}
            #filters["scenario_id"] = filters["task_id"]
            #del filters["task_id"]
=======
            filters["scenario__task_id"] = filters["task_id"]
            del filters["task_id"]
>>>>>>> dev
        except KeyError:
            filters = {}
        return query.filter(**filters)

    def _get_mvt_geom_query(self, x, y, z):
        bbox = Polygon.from_bbox(tile_edges(x, y, z))
        bbox.srid = 4326
        query = self.annotate(
            mvt_geom=AsMVTGeom(Transform(self.geo_col, 3857), Transform(bbox, 3857), 4096, 0,
                               False))
        intersect = {f"{self.geo_col}__intersects": bbox}
        return query.filter(**intersect)

    def _build_mvt_query(self, x, y, z, filters):
        """
        Creates MVT query

        Parameters
        ----------
        x : int
            x-coordinate of tile
        y : int
            y-coordinate of tile
        z : int
            z-coordinate of tile
        filters : dict
            keys represent column names and values represent column values to filter on.

        Returns
        -------
        tuple
            A tuple of length two.  The first element is a string representing a
            parameterized SQL query WHERE clause.  The second element is a list
            of parameters used as inputs to the WHERE clause.

        Raises
        ------
        ValidationError
            if sql query cannot be build with given parameters
        """
        query = self._get_mvt_geom_query(x, y, z)
        query = self._filter_query(query, x, y, z, filters)

        try:
            sql, params = query.query.sql_with_params()
        except FieldError as error:
            raise ValidationError(str(error)) from error
        with connection.cursor() as cursor:
            intermediate = cursor.mogrify(sql, params)
            print(intermediate)
            if isinstance(intermediate,str):
                return intermediate
            else:
                return intermediate.decode("utf-8")

    def _get_non_geom_columns(self):
        """
        Retrieves all table columns that are NOT the defined geometry column

        Returns
        -------
        list-of-str
            List of column names (excluding geom)
        """
        columns = []
        for field in self.model._meta.get_fields():
            if hasattr(field, "get_attname_column"):
                column_name = field.get_attname_column()[1]
                if column_name != self.geo_col:
                    columns.append(column_name)
        return columns


class LabelMVTManager(MVTManager):
    def get_queryset(self):
        return super().get_queryset().annotate(geom_label=models.functions.Centroid("geom"))
