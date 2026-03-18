from datetime import datetime

from .deepcopy import deepcopy as deepcopy_db

from django.test import TransactionTestCase, override_settings

from ebustoolbox.models import (
    User,
    Scenario,
    Event,
    Rotation,
    Trip,
    EventType,
    Vehicle,
    VehicleType,
    Plan,
    AssocPlanProcess,
)
from ebustoolbox.tests import build_scenario
from ebustoolbox.tasks import run_toolchain_from_scenario
from ebustoolbox.util import get_unique_task_id

from .models import Progress


class TestDeepCopy(TransactionTestCase):
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @override_settings(DEBUG=True)
    def test_deepcopy(self):
        django_scenario, simba_schedule, args = build_scenario()
        args.skip_flex_report = False
        django_scenario.options = vars(args)
        django_scenario.save()
        django_scenario.refresh_from_db()
        s1 = django_scenario
        s1.task_id = get_unique_task_id()
        s1.save()
        run_toolchain_from_scenario(s1)
        event = Event(
            scenario=s1,
            vehicle_type=Vehicle.objects.filter(scenario=s1).first().vehicle_type,
            vehicle=Vehicle.objects.filter(scenario=s1).first(),
            station=None,
            trip=Trip.objects.filter(scenario=s1).first(),
            area=None,
            subloc_no=1,
            time_start=datetime.now(),
            time_end=datetime.now(),
            soc_start=0.2,
            soc_end=0.8,
            event_type=EventType.CHARGING_DEPOT.value,
            description="Charging Event",
            timeseries={"time": [0, 1, 2], "soc": [0.2, 0.5, 0.8]},
        )
        event.save()
        s1.task_id = get_unique_task_id()
        s2, _ = deepcopy_db(
            s1,
            exclude_models={Scenario, User, Event, Progress},
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

        v1 = VehicleType.objects.filter(scenario=s1)
        v2 = VehicleType.objects.filter(scenario=s2)
        assert v1.count() == v2.count()
        v1 = v1.first()
        v2 = v2.first()
        compare_objects_except_related(v1, v2)
        vc1 = v1.vehicle_classes.first()
        vc2 = v2.vehicle_classes.first()
        compare_objects_except_related(vc1, vc2)
        assert vc1.scenario_id == s1.id
        assert vc2.scenario_id == s2.id

        apps1 = AssocPlanProcess.objects.filter(scenario=s1)
        apps2 = AssocPlanProcess.objects.filter(scenario=s2)
        assert apps1.count() == apps2.count()
        app1 = apps1.first()
        app2 = apps2.first()
        compare_objects_except_related(app1, app2)

        plan1 = Plan.objects.filter(scenario=s1).first()
        plan2 = Plan.objects.filter(scenario=s2).first()
        assert plan1.processes.count() == plan2.processes.count()
        assert plan2.processes.first().scenario_id == s2.id


def compare_objects_except_related(o1, o2):
    for field in o1.__class__._meta.fields:
        if field.name in ["id"] or field.related_model is not None:
            continue
        assert getattr(o1, field.name) == getattr(o2, field.name), f"Failed for {field.name}"
