import base64
from celery import uuid
from io import BytesIO
import matplotlib
from matplotlib import pyplot as plt
import pandas as pd
import sys

import django
from django.db.models import Max

from .models import Consumption, Scenario
from dash_app.data import get_powerdraw_as_dataframe

if not any(["selenium" in str(x) for x in sys.modules.values()]):
    # do not use tkagg during testing since it does not work with headless selenium
    # Explicitly call backend. Put into env? Without simba does not always properly generate plots
    matplotlib.use("Agg")


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
        x="time_start", y="Power", xlabel="Zeit", ylabel="Leistung [kW]", legend=False
    )
    buffer = BytesIO()
    ax.figure.savefig(buffer, format="png")
    buffer.seek(0)
    image_png = buffer.getvalue()
    buffer.close()
    plot = base64.b64encode(image_png)
    plot = plot.decode("utf-8")
    return plot


def get_next_id(manager: django.db.models.Manager) -> int:
    if manager.objects.exists():
        return manager.objects.aggregate(Max("id"))["id__max"] + 1
    return 1


def generate_consumption_lut_plot(consumption: Consumption):
    X_AXIS = "mean_speed_kmh"
    Y_AXIS = "consumption_kwh_per_km"
    CONSTANT_VALUES = [
        ("level_of_loading", 0.5),
        ("incline", 0.0),
    ]
    df = consumption.to_df()
    for column in [X_AXIS, Y_AXIS, *[t[0] for t in CONSTANT_VALUES]]:
        assert column in df.columns, f"Column {column} is missing in dataframe"

    df_reduced = df.copy()
    for column, value in CONSTANT_VALUES:
        mask = df_reduced.loc[:, column] == value
        df_reduced = df_reduced.loc[mask, :]
        if not any(mask):
            print(f"Column {column} with value {value} not found")
            return
    df = df_reduced
    figure, ax = generate_2d_plot_from_lut(df_reduced, x_axis=X_AXIS, y_axis=Y_AXIS)
    ax.set_xlabel("Speed [km/h]")
    ax.set_ylabel("Consumption [kWh/km]")
    title = str(consumption.name)
    second_half = title[len(title) // 2 :]
    i = second_half.find(" ")
    linebreak = len(title) // 2 + i
    title = title[:linebreak] + "\n" + title[linebreak:]
    ax.set_title(title)
    ax.legend(loc="upper right")
    descr = "\n".join((("=".join(str(x) for x in t) for t in CONSTANT_VALUES)))
    ax.annotate(
        descr,
        (1, 0.5),
        ha="right",
        xycoords="axes fraction",
        xytext=(0, 0),
        textcoords="offset points",
    )
    return figure


def generate_2d_plot_from_lut(df: pd.DataFrame, x_axis: str, y_axis: str):
    x_axis = "mean_speed_kmh"
    y_axis = "consumption_kwh_per_km"
    # Get remaining variations
    variation_column = None
    for column in df.columns:
        if column in [x_axis, y_axis]:
            continue
        if len(df.loc[:, column].unique()) > 1:
            assert (
                variation_column is None
            ), f"Plot cannot have two variation columns {variation_column} and {column}"
            variation_column = column

    for x_value in df.loc[:, x_axis].unique():
        mask = df.loc[:, x_axis] == x_value
        # Make sure that the variation column is unique per x_axis value.
        assert len(df.loc[mask, variation_column].unique()) == len(df.loc[mask, variation_column])
    print("creating plot")
    fig, ax = plt.subplots()
    ax.set_xlabel(x_axis)
    ax.set_ylabel(y_axis)
    for variation_value in df.loc[:, variation_column].unique()[::2]:
        mask = df.loc[:, variation_column] == variation_value
        df_reduced = df.loc[mask, :]
        ax.plot(
            df_reduced.loc[:, x_axis],
            df_reduced.loc[:, y_axis],
            marker="o",
            ls=":",
            lw=2,
            label=f"{variation_column}={variation_value}",
        )

    return fig, ax
