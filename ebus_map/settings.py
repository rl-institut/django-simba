from django_mapengine import setup
from django_mapengine.setup import Zoom  # noqa: F401

MAP_ENGINE_CENTER_AT_STARTUP = [13, 52]
MAP_ENGINE_ZOOM_AT_STARTUP = 8

MAP_ENGINE_IMAGES = [
    setup.MapImage("busstop", "ebus_map/static/ebus_map/bus_stop.jpg"),
    setup.MapImage("busstop_red", "ebus_map/static/ebus_map/bus_stop_red.png"),
    setup.MapImage("busstop_blue", "ebus_map/static/ebus_map/bus_stop_bl.png"),
]

# MAP_ENGINE_API_MVTS = {}
MAP_ENGINE_API_MVTS = {
    "stations_mvt": [
        # create layer called station, from app ebus_map with the model data from station
        setup.MVTAPI("station", "ebustoolbox", "station"),
        # setup.MVTAPI("routes", "ebustoolbox", "route"),
        setup.MVTAPI("myexamplemultipolygon", "HPCtool", "BusOutline"),
    ],
    "routes_mvt": [
        # setup.MVTAPI("station", "ebustoolbox", "station"),
        setup.MVTAPI("routes", "ebustoolbox", "route"),
    ],
}

MAP_ENGINE_API_CLUSTERS = [
    # setup.ClusterAPI("wind", "map", "MyExamplePoint"),
]

MAP_ENGINE_STYLES_FOLDER = "django_mapengine/static/django_mapengine/"
MAP_ENGINE_LAYERS_AT_STARTUP = ["station", "routes" , "myexamplemultipolygon"]
# These zoom levels define, where the specific features, e.g. points, lines choropleths (?) are
# visible
MAP_ENGINE_MIN_ZOOM = 1
MAP_ENGINE_ZOOM_LEVELS = {}
# These layers will be plotted as region, i.e. use layer_styles of region with outline and fill
REGIONS = []
MAP_ENGINE_POPUPS = [
    setup.Popup("station", True),
    # setup.Popup(
    #     "myexamplemultipolygon",
    #     True,
    #     ["population", ], ),
    # setup.Popup("wind",
    #             True,
    #             ["population", ], ),
    # setup.Popup("wind_cluster",
    #             True,
    #             ["population", ], )
]
MAP_ENGINE_USE_DISTILLED_MVTS = False
MAP_ENGINE_DEBUG = False
