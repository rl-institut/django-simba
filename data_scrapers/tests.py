from django.contrib.staticfiles.testing import StaticLiveServerTestCase
import numpy as np
import logging
from datetime import datetime

from django.urls import reverse
from django.utils.timezone import make_aware

from data_scrapers.models import BusStation, AdminArea
from data_scrapers.tasks import (
    get_antipodals,
    is_delimited,
    rotating_caliper,
    strip_delimiters,
    get_lower_admin_areas,
    search_station,
)
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.contrib.gis.geos import Point

logger = logging.getLogger("custom")


# SimpleTestCase since no DB access is needed
class RotatingCaliperTest(SimpleTestCase):
    @staticmethod
    def distance(p1, p2):
        return np.power(np.sum(np.power(np.subtract(p1, p2), 2)), 1 / 2)

    def test_square(self):
        """Test antipodal pairs on a square."""
        convex_hull = [(0, 0), (0, 2), (2, 2), (2, 0)]
        result = get_antipodals(convex_hull)
        for i, res in enumerate(result):
            assert res[0] == convex_hull[i]
            assert res[1] == convex_hull[(i + 1) % len(convex_hull)]
            assert res[2][0] == convex_hull[(i + 2) % len(convex_hull)]
            assert res[2][1] == convex_hull[(i + 3) % len(convex_hull)]

    def test_triangle(self):
        """Test antipodal pairs on a triangle."""
        convex_hull = [(0, 0), (1, 2), (2, 0)]
        result = get_antipodals(convex_hull)
        for i, res in enumerate(result):
            assert res[0] == convex_hull[i]
            assert res[1] == convex_hull[(i + 1) % len(convex_hull)]
            assert res[2][0] == convex_hull[(i + 2) % len(convex_hull)]

    def test_furthest_points(self):
        convex_hull = [(0, 0), (0.9, 2), (2, 0)]
        point1, point2 = rotating_caliper(convex_hull)
        assert point1 == (0.9, 2)
        assert point2 == (2, 0)

        steps = np.linspace(0, np.pi, 101)
        half_circle = list(zip(np.sin(steps), np.cos(steps)))
        extra_point = [0, 0.5]
        point1, point2 = rotating_caliper(half_circle + [extra_point])
        assert point1 == half_circle[0]
        assert point2 == half_circle[-1]
        # Distance is the diameter of the unitcircle
        assert self.distance(point1, point2) == 2

        half_circle = list(zip(np.sin(steps), np.cos(steps)))
        extra_point = [-1.5, 0]
        point1, point2 = rotating_caliper(half_circle + [extra_point])
        assert point1 == half_circle[int(len(steps) // 2)]
        assert point2 == extra_point
        # Calculate distance. Should be 2.5 since extra_point should connect with half circle
        # intersection at [1,0]
        assert self.distance(point1, point2) == 2.5


class StringHandlingTest(SimpleTestCase):
    def test_is_delimited(self):
        substring = "bar"
        assert is_delimited("bar foo", substring)
        assert is_delimited("(bar)foo", substring)
        assert is_delimited("bar/foo", substring)
        assert is_delimited(" bar foo", substring)
        assert is_delimited("foo bar foo", substring)
        assert is_delimited("foo bar", substring)

        assert not is_delimited("foobarfoo", substring)
        assert not is_delimited("foobar foo", substring)
        assert not is_delimited("foo barfoo", substring)
        assert not is_delimited("barfoo", substring)

    def test_strip_delimiters(self):
        assert strip_delimiters("foo") == "foo"
        assert strip_delimiters(", foo ,/") == "foo"
        assert strip_delimiters("(foo)") == "foo"
        assert strip_delimiters("()[[],]foo)") == "foo"
        # Is not expected to be an delimiter
        assert strip_delimiters("()[[].foo)") == ".foo"
        # Just like other special characters
        # Stripping stops after the first non strippable character
        assert strip_delimiters("§()[[].foo)") == "§()[[].foo"


class SearchUtilTest(TransactionTestCase):
    def setUp(self) -> None:
        now = make_aware(datetime.now())
        berlin = AdminArea.objects.create(
            name="Berlin", osm_id=1, admin_level=4, upper_admin_area=None, updated_at=now
        )
        mitte = AdminArea.objects.create(
            name="Mitte", osm_id=2, admin_level=8, upper_admin_area=berlin, updated_at=now
        )
        AdminArea.objects.create(
            name="Moabit", osm_id=3, admin_level=9, upper_admin_area=mitte, updated_at=now
        )
        AdminArea.objects.create(
            name="Wedding", osm_id=4, admin_level=9, upper_admin_area=mitte, updated_at=now
        )

    def test_get_lower_admin_areas(self):
        assert get_lower_admin_areas(AdminArea.objects.none()).count() == 0
        berlin = AdminArea.objects.filter(name="Berlin")
        mitte = AdminArea.objects.filter(name="Mitte")
        moabit = AdminArea.objects.filter(name="Moabit")
        _ = AdminArea.objects.filter(name="Wedding")

        # Including all 3 children + berlin itself -> 4 children
        assert get_lower_admin_areas(berlin).count() == 4

        # Including all 2 children + mitte itself -> 3 children
        assert get_lower_admin_areas(mitte).count() == 3

        # moabit is admin_level 9.
        # Since at least admin_level 8 is looked up the result is equal to above-> 3 children
        assert get_lower_admin_areas(moabit).count() == 3


class StationSearchTest(StaticLiveServerTestCase):
    def setUp(self) -> None:
        now = make_aware(datetime.now())
        berlin = AdminArea.objects.create(
            name="Berlin", osm_id=1, admin_level=4, upper_admin_area=None, updated_at=now
        )
        berlin_mitte = AdminArea.objects.create(
            name="Mitte", osm_id=2, admin_level=6, upper_admin_area=berlin, updated_at=now
        )
        brandenburg = AdminArea.objects.create(
            name="Brandenburg",
            osm_id=3,
            admin_level=4,
            upper_admin_area=None,
            updated_at=now,
        )
        brandenburg_mitte = AdminArea.objects.create(
            name="Mitte", osm_id=4, admin_level=4, upper_admin_area=brandenburg, updated_at=now
        )

        # BusStation in Berlin/ Mitte
        BusStation.objects.create(
            name="Alexanderplatz", osm_id=101, admin_area=berlin_mitte, geom=Point(10, 10, 0)
        )
        # BusStation in Brandenburg
        BusStation.objects.create(
            name="Alexanderplatz", osm_id=102, admin_area=brandenburg, geom=Point(20, 10, 10)
        )
        # BusStation in Brandenburg / Mitte
        BusStation.objects.create(
            name="Alexanderplatz", osm_id=103, admin_area=brandenburg_mitte, geom=Point(40, 10, 30)
        )

        # BusStation with slightly different name in Brandenburg / Mitte
        BusStation.objects.create(
            name="AleKanderplatz", osm_id=104, admin_area=brandenburg_mitte, geom=Point(40, 10, 30)
        )

        # BusStation with slightly different name in Brandenburg / Mitte
        BusStation.objects.create(
            name="Berliner Str", osm_id=105, admin_area=brandenburg_mitte, geom=Point(40, 10, 30)
        )
        # BusStation with slightly different name in Brandenburg / Mitte
        BusStation.objects.create(
            name="Berliner Straße", osm_id=106, admin_area=brandenburg_mitte, geom=Point(40, 10, 30)
        )

    @override_settings(DEBUG=True)
    def test_station_search(self):
        # 3 Stations with exact matching Station names are found
        found_stations = search_station(
            "Alexanderplatz", possible_admins_names=[], return_all=False, filter_stack=lambda x: x
        )
        assert found_stations.count() == 3

        # The searched name is slightly misspelled. Since no exact matches are returned early,
        # all 4 Stations are found, since they fuzzily match the search
        found_stations = search_station(
            "Aleanderplatz", possible_admins_names=[], return_all=False, filter_stack=lambda x: x
        )
        assert found_stations.count() == 4

        # The name is slightly misspelled but favors Alekanderplatz.
        found_stations = search_station(
            "Alekanderplat", possible_admins_names=[], return_all=False, filter_stack=lambda x: x
        )
        assert found_stations.count() == 1

        # With the return_all=True all similar named stations are returned
        found_stations = search_station(
            "Alekanderplat", possible_admins_names=[], return_all=True, filter_stack=lambda x: x
        )
        assert found_stations.count() == 4

        # With return_all=False only the most likely stations are returned.
        # The name is slightly misspelled but favors Alexanderplatz.
        found_stations = search_station(
            "Alexanderplat", possible_admins_names=[], return_all=False, filter_stack=lambda x: x
        )
        assert found_stations.count() == 3

        # Some abbreviations are found too
        found_stations = search_station(
            "Alekanderpl.", possible_admins_names=[], return_all=True, filter_stack=lambda x: x
        )
        assert found_stations.count() == 1

        # Maybe the station name is given with an AdminArea name as prefix or suffix
        # If no possible_admin_names are given the name will fuzzily match all Alexanderplatz
        # Stations, even if they are not in Brandenburg
        found_stations = search_station(
            "Brandenburg Alexanderplatz",
            possible_admins_names=[],
            return_all=False,
            filter_stack=lambda x: x,
        )
        assert found_stations.count() == 3
        assert "Berlin" in found_stations.values_list(
            "admin_area__upper_admin_area__name", flat=True
        )

        # If "Brandenburg" is passed as possible_admin_name,
        # AdminAreas with this name or children from it will be searched,
        # if the station has this admin name as substring.
        # This should result in 2 matches with the exact name Alexanderplatz in Brandenburg.
        found_stations = search_station(
            "Brandenburg Alexanderplatz",
            possible_admins_names=["Brandenburg"],
            return_all=False,
            filter_stack=lambda x: x,
        )
        assert found_stations.count() == 2
        assert "Berlin" not in found_stations.values_list(
            "admin_area__upper_admin_area__name", flat=True
        )
        brandenburg_ids = list(found_stations.values_list("id", flat=True))

        # This should also work with various Formatting
        search_strings = [
            "(Brandenburg) Alexanderplatz",
            "Brandenburg - Alexanderplatz",
            "Brandenburg/Alexanderplatz",
            "Brandenburg, Alexanderplatz",
        ]
        for search_string in search_strings:
            search_string = "(Brandenburg) Alexanderplatz"
            found_stations = search_station(
                search_string,
                possible_admins_names=["Brandenburg"],
                return_all=False,
                filter_stack=lambda x: x,
            )
            assert list(found_stations.values_list("id", flat=True)) == brandenburg_ids

        found_stations = search_station(
            "Berliner Str",
            possible_admins_names=[],
            return_all=True,
            filter_stack=lambda x: x,
        )
        assert found_stations.count() == 2

    @override_settings(DEBUG=True)
    def test_stations_search_api(self):
        url = f"{self.live_server_url}{reverse('data_scrapers:busstation_api')}"
        logger.warning("Using url: ", url)
        params = {"search_stations": "Alexanderplatz|Alekanderplatz"}
        # API does not work without a search query
        response = self.client.get(url, params)
        assert response.status_code == 400, f"Unexpected response code {response.status_code}"
        assert response.status_code == 200
        data = response.json()
        assert "results" in data

        assert "Alekanderplatz" in data["results"]
        assert "Alexanderplatz" in data["results"]
        assert len(data["results"]["Alekanderplatz"]) == 1
        assert len(data["results"]["Alexanderplatz"]) == 3

        search_strings = [
            "Berlin Alexanderplatz",
            "Berlin, Alexanderplatz",
            "(Berlin) Alexanderplatz",
        ]
        for search in search_strings:
            search = search_strings[-1]
            params = {"search_stations": search}
            response = self.client.get(url, params)
            data = response.json()
            assert len(data["results"][search]) == 1
