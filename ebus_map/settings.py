from django_mapengine import setup
from django_mapengine.setup import Zoom  # noqa: F401

MAP_ENGINE_CENTER_AT_STARTUP = [13, 52]
MAP_ENGINE_ZOOM_AT_STARTUP = 8

MAP_ENGINE_IMAGES = [
    setup.MapImage("station_icon", "ebus_map/bus_stop.jpg"),
    setup.MapImage("station_icon_red", "ebus_map/bus_stop_red.png"),
    setup.MapImage("station_icon_png", "ebus_map/bus_stop.png"),
]

# MAP_ENGINE_API_MVTS = {}
MAP_ENGINE_API_MVTS = {
    "stations_mvt": [
        setup.MVTAPI("station", "ebus_map", "Station"),
    ],
}

MAP_ENGINE_API_CLUSTERS = [
    # setup.ClusterAPI("wind", "map", "MyExamplePoint"),
]

MAP_ENGINE_STYLES_FOLDER = "ebus_map/static/ebus_map/"
MAP_ENGINE_LAYERS_AT_STARTUP = ["station"]  # "myexamplemultipolygon"]
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
