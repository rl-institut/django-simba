from dash import Dash, html, dcc
import plotly.express as px
from . import ids
from dash.dependencies import Input, Output
from ebustoolbox.models import Scenario
import pandas as pd
import numpy as np
from . import data
from .colorscheme import color_scheme
import plotly.graph_objects as go


def render_soc(app: Dash) -> html.Div:
    """
    Renders a histogram of state of charge (SOC) for selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered SOC histogram.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.SOC_HISTOGRAM, "children"),
        Input(ids.BUS_DROPDOWN, "value"),
    )
    def update_soc_histogram(
        buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:

        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        soc_df = data.get_soc_as_dataframe(s.id, buses)

        # Calculate alpha value
        alpha = 1 / len(soc_df["V_id"].unique())

        # Binning SOCs per vehicle in 0.05 bins
        bins = []
        for vehicle_id, group in soc_df.groupby("V_id"):
            hist, bin_edges = np.histogram(
                group["soc_end"], bins=np.arange(-0.1, 1.1, 0.1), density=True
            )
            for i, b in enumerate(hist):
                bins.append({"V_id": vehicle_id, "Bin": bin_edges[i], "Frequency": b})

        # Normalize such that the sum of heights of all bins equals 100% for each vehicle
        normalized_bins = []
        for vehicle_id, group in pd.DataFrame(bins).groupby("V_id"):
            total_frequency = group["Frequency"].sum()
            for index, row in group.iterrows():
                normalized_bins.append(
                    {
                        "V_id": vehicle_id,
                        "Bin": row["Bin"],
                        "Frequency": row["Frequency"] / total_frequency * 100,
                    }
                )

        # Create a DataFrame for the normalized bins
        normalized_df = pd.DataFrame(normalized_bins)

        # Create a figure for the histogram following line is needed due to plotly bug,
        # see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
        fig = go.Figure(layout=dict(template="plotly"))
        fig = px.histogram(
            normalized_df,
            x="Bin",
            y="Frequency",
            color="V_id",
            barmode="overlay",
            opacity=alpha,
            color_discrete_sequence=color_scheme,
        )

        # Update layout to display bars in front of each other
        fig.update_layout(barmode="overlay")
        fig.update_layout(showlegend=False)
        fig.update_coloraxes(showscale=False)
        fig.update_xaxes(title_text="SOC")
        fig.update_yaxes(title_text="Relative Häufigkeit in %")

        return html.Div(dcc.Graph(figure=fig), id=ids.SOC_HISTOGRAM)

    return html.Div(id=ids.SOC_HISTOGRAM)


def render_dist_dur(app: Dash) -> html.Div:
    """
    Renders histograms of distance and duration for selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered distance and duration histograms.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.DIST_DUR_HISTOGRAM, "children"),
        Input(ids.BUS_DROPDOWN, "value"),
    )
    def dist_dur_histogram(
        buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        dur_df = data.get_duration_as_dataframe(s.id, buses)
        dist_df = data.get_distances_as_dataframe(s.id, buses)

        dur_df["dist_per_dur"] = dist_df["total_distance"] / dur_df["duration"]

        # Set the desired bin width
        # bin_width = 0.5  # Specify your desired bin width here
        # Calculate the number of bins based on the bin width
        # max_distance = dur_df["dist_per_dur"].max()
        # min_distance = dur_df["dist_per_dur"].min()
        # num_bins = int((max_distance - min_distance) / bin_width)

        # following line is needed due to plotly bug,
        # see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
        fig = go.Figure(layout=dict(template="plotly"))
        fig = px.histogram(
            dur_df,
            x="dist_per_dur",
            barmode="overlay",
            nbins=20,
            color_discrete_sequence=color_scheme,
        )

        # Update layout to display bars in front of each other
        fig.update_layout(barmode="overlay")
        fig.update_layout(showlegend=False)
        fig.update_coloraxes(showscale=False)
        fig.update_xaxes(
            title_text="Durchschnittsgeschwindigkeit für Umlauf (inkl. Stillstand, Pausen) in m/s"
        )
        fig.update_yaxes(title_text="Relative Häufigkeit in %")

        return html.Div(dcc.Graph(figure=fig), id=ids.DIST_DUR_HISTOGRAM)

    return html.Div(id=ids.DIST_DUR_HISTOGRAM)


def render_rotation_distance(app: Dash) -> html.Div:
    """
    Renders a histogram of rotation distances for selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered rotation distance histogram.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.DIST_HISTOGRAM, "children"),
        Input(ids.BUS_DROPDOWN, "value"),
    )
    def update_distances_histogram(
        buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        df = data.get_distances_as_dataframe(s.id, buses)

        # Set the desired bin width
        bin_width = 20000  # Specify your desired bin width here

        # Calculate the number of bins based on the bin width
        max_distance = df["total_distance"].max()
        min_distance = df["total_distance"].min()

        num_bins = int((max_distance - min_distance) / bin_width)

        # following line is needed due to plotly bug,
        # see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
        fig = go.Figure(layout=dict(template="plotly"))
        fig = px.histogram(
            df,
            x="total_distance",
            nbins=num_bins,
            barmode="overlay",
            color_discrete_sequence=color_scheme,
        )

        # Update layout to display bars in front of each other
        fig.update_layout(barmode="overlay")
        fig.update_layout(showlegend=False)
        fig.update_coloraxes(showscale=False)
        fig.update_xaxes(title_text="Distanz (Nur Trips)")
        fig.update_yaxes(title_text="Absolute Häufigkeit")

        return html.Div(dcc.Graph(figure=fig), id=ids.DIST_HISTOGRAM)

    return html.Div(id=ids.DIST_HISTOGRAM)


def render_rotation_duration(app: Dash) -> html.Div:
    """
    Renders a histogram of rotation durations for selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered rotation duration histogram.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.DUR_HISTOGRAM, "children"),
        Input(ids.BUS_DROPDOWN, "value"),
    )
    def update_distances_histogram(
        buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        df = data.get_duration_as_dataframe(s.id, buses)

        # Set the desired bin width
        # bin_width = 100  # Specify your desired bin width here

        # Calculate the number of bins based on the bin width
        # max_duration = df["duration"].max()
        # min_duration = df["duration"].min()
        # num_bins = int((max_duration - min_duration) / bin_width)

        # following line is needed due to plotly bug,
        # see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
        fig = go.Figure(layout=dict(template="plotly"))
        fig = px.histogram(
            df, x="duration", barmode="overlay", nbins=20, color_discrete_sequence=color_scheme
        )

        # Update layout to display bars in front of each other
        fig.update_layout(barmode="overlay")
        fig.update_layout(showlegend=False)
        fig.update_coloraxes(showscale=False)
        fig.update_xaxes(title_text="Dauer (Nur Trips)")
        fig.update_yaxes(title_text="Absolute Häufigkeit")

        return html.Div(dcc.Graph(figure=fig), id=ids.DUR_HISTOGRAM)

    return html.Div(id=ids.DUR_HISTOGRAM)


def render_minimal_soc(app: Dash) -> html.Div:
    """
    Renders a histogram of minimal state of charge (SOC) for selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered minimal SOC histogram.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.MIN_SOC_HISTOGRAM, "children"),
        Input(ids.BUS_DROPDOWN, "value"),
    )
    def update_soc_histogram(
        buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        soc_df = data.get_soc_as_dataframe(s.id, buses)

        min_soc_per_v_id = soc_df.groupby("V_id")["soc_end"].min().reset_index()

        # Create a figure for the histogram following line is needed due to plotly bug,
        # see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
        fig = go.Figure(layout=dict(template="plotly"))
        # Plot histogram using Plotly Express
        fig = px.histogram(
            min_soc_per_v_id,
            x="soc_end",
            title="Minimum SOC per Vehicle",
            nbins=20,
            barmode="overlay",
            color_discrete_sequence=color_scheme,
        )

        # Update layout to display bars in front of each other
        fig.update_layout(barmode="overlay")
        fig.update_layout(showlegend=False)
        fig.update_coloraxes(showscale=False)
        fig.update_xaxes(title_text="minimal SOC")
        fig.update_yaxes(title_text="Relative Häufigkeit in %")

        return html.Div(dcc.Graph(figure=fig), id=ids.MIN_SOC_HISTOGRAM)

    return html.Div(id=ids.MIN_SOC_HISTOGRAM)
