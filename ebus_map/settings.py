from django_mapengine import setup
from django_mapengine.setup import Zoom

MAP_ENGINE_CENTER_AT_STARTUP = [13, 52]
MAP_ENGINE_ZOOM_AT_STARTUP = 8
# MAP_ENGINE_MAX_BOUNDS = [[0.280733017118229, 48.22918643452503], [0.616574868700604, 55.35515806663738]]

MAP_ENGINE_IMAGES = [setup.MapImage("busstop", "django_mapengine/images/icons/bus_stop.png"),
                     setup.MapImage("busstop_red", "django_mapengine/images/icons/bus_stop_red.png"),
                     ]

# MAP_ENGINE_API_MVTS = {}
MAP_ENGINE_API_MVTS = {
    "busstop":
        [
            setup.MVTAPI("busstop", "ebus_map", "Station"),
        ],
}

MAP_ENGINE_API_CLUSTERS = [
    # setup.ClusterAPI("wind", "map", "MyExamplePoint"),
]

MAP_ENGINE_STYLES_FOLDER = "django_mapengine/static/django_mapengine/"
MAP_ENGINE_LAYERS_AT_STARTUP = ["busstop"]   # "myexamplemultipolygon"]
# These zoom levels define, where the specific features, e.g. points, lines choropleths (?) are
# visible
MAP_ENGINE_MIN_ZOOM = 1
MAP_ENGINE_ZOOM_LEVELS = {
    # "busstop": Zoom(0, 24),
    # "myexamplemultipolygon": Zoom(0, 24),
}
# These layers will be plotted as region, i.e. use layer_styles of region with outline and fill
REGIONS = []
MAP_ENGINE_POPUPS = [
    setup.Popup("busstop",
                True),
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
MAP_ENGINE_DEBUG = True
