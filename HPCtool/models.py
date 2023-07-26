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

    annotations = {"center": models.functions.Centroid("geom"),
                   "lat": X("center", output_field=models.DecimalField()),
                   "lon": Y("center", output_field=models.DecimalField())}


from django.contrib.gis.geos import MultiPolygon, Polygon

p_list = [
[(52.50946224876103, 13.332402018765666), (52.50932210268181, 13.332534535801337), (52.50930862680067, 13.332496254040304), (52.50944877283665, 13.332363736925283)],
[(52.50964132407587, 13.332232690205426), (52.50950117819935, 13.33236520832714), (52.50948770226294, 13.332326926464717), (52.50962784809622, 13.332194408263655)],
[(52.5098203991317, 13.332063360257468), (52.50968025345787, 13.332195879465228), (52.5096667774662, 13.332157597501412), (52.50980692309678, 13.332025078214304)],
[(52.509999452170824, 13.331897129488848), (52.509858597691824, 13.332027614951043), (52.509845328567664, 13.33198913921481), (52.50998618300383, 13.331858653670775)],
[(52.51017943266977, 13.33173039684045), (52.510038578387636, 13.331860883377804), (52.510025309208764, 13.331822407536995), (52.510166163448055, 13.331691920917802)],
[(52.50952454652967, 13.332209944839473), (52.50938550635542, 13.332345555164693), (52.50937171593229, 13.332307575432457), (52.50951075606269, 13.332171965031748)],
[(52.5097022087334, 13.332036663724304), (52.50956316877089, 13.33217227515162), (52.50954937829171, 13.332134295322925), (52.50968841821037, 13.33199868382012)],
[(52.50987987278436, 13.3318633881408), (52.50974083091337, 13.331998994830906), (52.509727040971946, 13.331961014326602), (52.50986608279908, 13.331825407560997)],
[(52.51005675005423, 13.331690896106286), (52.509917704446025, 13.33182649301936), (52.509903915554624, 13.331788511340584), (52.510042961118955, 13.331652914351999)],
[(52.509590770851766, 13.331988544670969), (52.509451004492426, 13.332122134937025), (52.509437419512665, 13.33208395678598), (52.509577185828554, 13.3319503664419)],
[(52.50976936096543, 13.331817844754354), (52.50962959481191, 13.331951436112105), (52.5096160097766, 13.331913257861366), (52.50975577588666, 13.331779666425593)],
[(52.509947949374705, 13.33164713861333), (52.50980818487416, 13.33178073512974), (52.509794599369755, 13.331742557174616), (52.509934363826844, 13.33160896058019)],
[(52.51012545626257, 13.331477462842562), (52.509985692207394, 13.33161106112072), (52.509972106578964, 13.331572883132282), (52.510111870590706, 13.331439284776108)],
[(52.50965871878946, 13.33177010322973), (52.50951920537573, 13.331904402014795), (52.50950554836695, 13.331866292897967), (52.50964506173709, 13.331731994035765)],
[(52.50983698396279, 13.331598492228828), (52.50969747249181, 13.331732796950222), (52.50968381493504, 13.331694688208724), (52.509823326362415, 13.3315603834102)],
[(52.51001461648448, 13.331427472769645), (52.50987510871778, 13.331561788340084), (52.50986145011324, 13.331523680455641), (52.51000095783633, 13.331389364808082)],
[(52.50973044054118, 13.331549489969554), (52.50958959779115, 13.331680008621158), (52.50957632521031, 13.331641536325446), (52.5097171679175, 13.331511017592044)],
[(52.509910287319414, 13.331382304033395), (52.50976956448587, 13.33151317017502), (52.509756256623156, 13.331474730477147), (52.509896979413796, 13.331343864254146)],
[(52.510089811517766, 13.33121517803188), (52.50994912910273, 13.331346161358574), (52.50993580937807, 13.331307732543554), (52.510076491750176, 13.331176749135624)],
[(52.50965930305733, 13.332377502054019), (52.50952030640183, 13.332513232584402), (52.509506503797404, 13.332475264622527), (52.509645500409015, 13.332339534016805)],
[(52.50983690971285, 13.332204067537308), (52.5096979132099, 13.332339799006634), (52.50968411056605, 13.33230183093223), (52.50982310702511, 13.332166099387564)],
[(52.51001415393536, 13.332031014052427), (52.50987515087168, 13.332166727991165), (52.509861350066444, 13.33212875797076), (52.51000035308624, 13.33199304395666)],
[(52.51019176630269, 13.33185759263039), (52.510052765870434, 13.331993314328045), (52.51003896433227, 13.331955344872227), (52.51017796472062, 13.33181962309922)],

]

import random


for p in p_list:
    #print(*p)
    a,b,c,d = p
    #print(a,b,c,d)
    p1 = Polygon((a[::-1],b[::-1],c[::-1],d[::-1],a[::-1]))
    #print(p1)




#p1 = Polygon(((21, 40), (0, 50), (50, 50), (21, 40)))
    mp = MultiPolygon(p1)
    #BusOutline.objects.create(geom=mp, name="buzz", scenario="neu", quality=random.randint(0, 2))

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


import geopandas as gpd
from shapely.geometry import Point

(lat, lon) = (52.509818902322095, 13.331913815793728)

s = gpd.GeoSeries([ Point(lon, lat),Point(lon, lat)], crs= 4326)

s = s.to_crs(25833)

sbbox = (
    float(s[1].x), float(s[1].y), float(s[0].x), float(s[0].y)
)



alkis = gpd.read_file("/home/patrick/Documents/HPC_Tool/SHP_BE_ALKIS_Merged/Flurstuecke_Flaechen.shp", bbox=sbbox)

alkis_4326 = alkis.to_crs(4326)

cdr = list(zip(*alkis_4326.geometry.values[0].exterior.coords.xy))

p2 = Polygon(cdr)
mp = MultiPolygon(p2)
#Flurstueck.objects.create(geom=mp, name="Herzallee", scenario="neu")




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




from django.contrib.gis.geos import Point

flurstück_bounds = (
    alkis.geometry.bounds['minx'].min(), alkis.geometry.bounds['miny'].min(), alkis.geometry.bounds['maxx'].max(),
    alkis.geometry.bounds['maxy'].max()
)
success, gdf = generalizedLayerLoader("https://fbinter.stadt-berlin.de/fb/wfs/data/senstadt/s_wfs_baumbestand","fis:s_wfs_baumbestand",flurstück_bounds)
if success >0:
    gdf_4326 = gdf.to_crs(4326)
    #print(gdf_4326)
    for row in gdf_4326.geometry:
        #print(list(zip(*row.xy)))
        p3 = Point(*list(zip(*row.xy)))
        #print(p3)
        #Tree.objects.create(geom=p3, name="Herzallee", scenario="neu")
else:
    print("ERROR")


#





