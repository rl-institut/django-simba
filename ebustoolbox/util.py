import base64

import django
from celery import uuid
from io import BytesIO
import matplotlib
import sys

from django.db.models import Max
from django.utils.translation import gettext as _

from .models import Scenario
from ebustoolbox.data import get_powerdraw_as_dataframe

if not any(["selenium" in str(x) for x in sys.modules.values()]):
    # do not use tkagg during testing since it does not work with headless selenium
    # Explicitly call backend. Put into env? Without simba does not always properly generate plots
    matplotlib.use("Agg")


# TODO: remove since checking uuid is not common since duplicates are too rare
def get_unique_task_id() -> str:
    task_id_not_unique = True
    task_id = None
    # Create unique ids for as long as needed, so no duplicate ids exist
    while task_id_not_unique:
        try:
            task_id = uuid()
            Scenario.objects.get(task_id=task_id)
        except Scenario.DoesNotExist:
            task_id_not_unique = False
    return task_id


def get_charge_chart(station):
    """
    Get charge plot for specific station, ready for HTML display
    """
    # get power at this station
    power_df = get_powerdraw_as_dataframe(station.scenario.id)
    power_df = power_df[power_df["Station_id"] == station.name]
    if power_df.empty:
        return None

    ax = power_df.plot(
        x=_("time_start"), y=_("Power"), xlabel=_("Zeit"), ylabel=_("Leistung [kW]"), legend=False
    )
    buffer = BytesIO()
    ax.figure.savefig(buffer, format="png")
    buffer.seek(0)
    image_png = buffer.getvalue()
    buffer.close()
    plot = base64.b64encode(image_png)
    plot = plot.decode("utf-8")
    return plot


def get_next_id(model: django.db.models.Model) -> int:
    if model.objects.exists():
        return model.objects.aggregate(Max("id"))["id__max"] + 1
    return 1
