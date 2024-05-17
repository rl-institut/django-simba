from .deepcopy import deepcopy as deepcopy_db
import io
import unittest.mock

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from ebus_map.models import Station
from ebustoolbox.models import Scenario, Event, Rotation, Trip
from ebustoolbox.tests import build_scenario
from ebustoolbox.tasks import run_toolchain_from_scenario
from ebustoolbox.util import get_unique_task_id

from .models import Progress


class TestDeepCopy(TransactionTestCase):
    def test_deepcopy(self):
        s1, simba_schedule, args = build_scenario()
        s1.task_id = get_unique_task_id()
        s1.save()
        run_toolchain_from_scenario(s1)

        s1.task_id = None
        s2, _ = deepcopy_db(
            s1,
            exclude_models={Scenario, User, Station, Event, Progress},
            max_depth=1,
        )
        assert Trip.objects.filter(scenario=s1).count() == Trip.objects.filter(scenario=s2).count()
        assert (
            Rotation.objects.filter(scenario=s1).count()
            == Rotation.objects.filter(scenario=s2).count()
        )

        assert (
            Event.objects.filter(scenario=s1).count() != Event.objects.filter(scenario=s2).count()
        )
        assert Event.objects.filter(scenario=s2).count() == 0

        r1 = Rotation.objects.filter(scenario=s1).first()
        r2 = Rotation.objects.filter(scenario=s2, name=r1.name).first()
        compare_objects_except_related(r1, r2)

        t1 = Trip.objects.filter(rotation=r1).first()
        t2 = Trip.objects.filter(rotation=r2).first()
        compare_objects_except_related(t1, t2)


def compare_objects_except_related(o1, o2):
    for field in o1.__class__._meta.fields:
        if field.name in ["id"] or field.related_model is not None:
            continue
        assert getattr(o1, field.name) == getattr(o2, field.name), f"Failed for {field.name}"


class TestEmail(TestCase):
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    @unittest.mock.patch('sys.stdout', new_callable=io.StringIO)
    def test_console_backend(self, mock_stdout):
        # create test users (normal and staff)
        User.objects.create_user(
            username='Adam', email='adam@example.com', password='adams_password')
        User.objects.create_superuser(
            username='Eve', email='eve@example.com', password='eves_password')

        # anon user: no email
        self.client.get(reverse("core:test_email"))
        assert mock_stdout.getvalue() == ''

        # normal user: no email
        self.client.login(username='Adam', password='adams_password')
        self.client.get(reverse("core:test_email"))
        assert mock_stdout.getvalue() == ''

        # staff user: mock email in console
        self.client.login(username='Eve', password='eves_password')
        self.client.get(reverse("core:test_email"))
        assert 'To: eve@example.com' in mock_stdout.getvalue()
