import numpy as np

from data_scrapers.tasks import get_antipodals, rotating_caliper
from django.test import TestCase


class RotatingCaliperTest(TestCase):
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

        steps = np.linspace(0, np.pi, 100)
        half_circle = list(zip(np.sin(steps), np.cos(steps)))
        extra_point = [0, 0.5]
        point1, point2 = rotating_caliper(half_circle + [extra_point])
        assert point1 == half_circle[0]
        assert point2 == half_circle[-1]

        half_circle = list(zip(np.sin(steps), np.cos(steps)))
        extra_point = [0, -1.5]
        point1, point2 = rotating_caliper(half_circle + [extra_point])
        assert point1 == half_circle[0]
        assert point2 == extra_point

        half_circle = list(zip(np.sin(steps), np.cos(steps)))
        extra_point = [-1.5, 0]
        point1, point2 = rotating_caliper(half_circle + [extra_point])
        assert point1 == half_circle[49]
        assert point2 == extra_point
