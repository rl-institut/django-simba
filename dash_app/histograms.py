from dash import Dash, html, dcc
import plotly.express as px
import plotly.graph_objects as go
from . import ids, data
from dash.dependencies import Input, Output, State
from ebustoolbox.models import Scenario
import pandas as pd
from .colorscheme import color_scheme
from .data import get_critical_rotations_and_score_as_dataframe
from .style import set_styling


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
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    @set_styling
    def dist_dur_histogram(
        _, buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        dur_df = data.get_duration_as_dataframe(s.id, buses)
        dist_df = data.get_distances_as_dataframe(s.id, buses)

        # Calculate average speed in km/h
        dur_df["avg_speed_kmh"] = (dist_df["total_distance"] / 1000) / (dur_df["duration"] / 3600)

        # Set the desired bin width in km/h
        bin_width_kmh = 2.5  # Specify your desired bin width in km/h here

        # Calculate the number of bins based on the bin width
        max_speed_kmh = dur_df["avg_speed_kmh"].max()
        min_speed_kmh = dur_df["avg_speed_kmh"].min()
        num_bins = int((max_speed_kmh - min_speed_kmh) / bin_width_kmh)

        # Following line is needed due to plotly bug,
        # see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
        fig = go.Figure(layout=dict(template="plotly"))

        fig = px.histogram(
            dur_df,
            x="avg_speed_kmh",
            nbins=num_bins,
            barmode="overlay",
            color_discrete_sequence=color_scheme,
        )

        # Update layout to display bars in front of each other
        fig.update_layout(
            barmode="overlay",
            showlegend=False,
            coloraxis_showscale=False,
            xaxis_title="Durchschnittsgeschwindigkeit (km/h)",
            yaxis_title="Absolute Häufigkeit",
            title="Verteilung der Durchschnittsgeschwindigkeit der Umläufe inkl. Stops, Pausen",
            margin=dict(l=20, r=20, t=40, b=20),
        )

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
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    @set_styling
    def update_distances_histogram(
        _, buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        df = data.get_distances_as_dataframe(s.id, buses)

        # Convert total_distance from meters to kilometers
        df["total_distance_km"] = df["total_distance"] / 1000

        # Set the desired bin width in kilometers
        bin_width_km = 5  # Specify your desired bin width in kilometers here, the final bin with is twice this value

        # Calculate the number of bins based on the bin width
        max_distance_km = df["total_distance_km"].max()
        min_distance_km = df["total_distance_km"].min()
        num_bins = int((max_distance_km - min_distance_km) / bin_width_km)

        # Following line is needed due to plotly bug,
        # see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
        fig = go.Figure(layout=dict(template="plotly"))

        fig = px.histogram(
            df,
            x="total_distance_km",
            nbins=num_bins,
            barmode="overlay",
            color_discrete_sequence=color_scheme,
        )

        # Update layout to display bars in front of each other
        fig.update_layout(
            barmode="overlay",
            showlegend=False,
            coloraxis_showscale=False,
            xaxis_title="Distanz (km)",
            yaxis_title="Absolute Häufigkeit",
            title="Verteilung der Umlaufdistanzen ",
            margin=dict(l=20, r=20, t=40, b=20),
        )

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
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    @set_styling
    def update_distances_histogram(
        _, buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        df = data.get_duration_as_dataframe(s.id, buses)

        # Convert the duration from seconds to hours
        df["duration"] = df["duration"] / 3600

        # Set the desired bin width in hours
        bin_width_hours = 0.5  # Specify your desired bin width in hours here

        # Calculate the number of bins based on the bin width
        max_duration = df["duration"].max()
        min_duration = df["duration"].min()
        num_bins = int((max_duration - min_duration) / bin_width_hours)

        # Following line is needed due to plotly bug,
        # see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
        fig = go.Figure(layout=dict(template="plotly"))

        fig = px.histogram(
            df,
            x="duration",
            barmode="overlay",
            nbins=num_bins,
            color_discrete_sequence=color_scheme,
        )

        # Update layout to display bars in front of each other
        fig.update_layout(barmode="overlay")
        fig.update_layout(showlegend=False)
        fig.update_coloraxes(showscale=False)
        fig.update_xaxes(title_text="Dauer (h)")
        fig.update_yaxes(title_text="Absolute Häufigkeit")
        fig.update_layout(title="Verteilung der Umlaufdauer", margin=dict(l=20, r=20, t=40, b=20))

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
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    @set_styling
    def update_soc_histogram(
        _, buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        # Adjust display settings
        pd.set_option("display.max_columns", None)  # Show all columns
        pd.set_option("display.expand_frame_repr", False)  # Prevent line wrapping

        soc_df = data.get_soc_as_dataframe(s.id, buses)

        min_soc_per_v_id = soc_df.groupby("V_id")["soc_end"].min().reset_index()

        # Set the desired bin width
        bin_width = 0.05  # Specify your desired bin width here

        # Calculate the number of bins based on the bin width
        max_soc = min_soc_per_v_id["soc_end"].max()
        min_soc = min_soc_per_v_id["soc_end"].min()
        num_bins = int((max_soc - min_soc) / bin_width)

        # Following line is needed due to plotly bug,
        # see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
        fig = go.Figure(layout=dict(template="plotly"))

        fig = px.histogram(
            min_soc_per_v_id,
            x="soc_end",
            title="Minimaler SOC pro Fahrzeug",
            nbins=num_bins,
            barmode="overlay",
            color_discrete_sequence=color_scheme,
        )

        # Update layout to display bars in front of each other
        fig.update_layout(
            barmode="overlay",
            showlegend=False,
            coloraxis_showscale=False,
            xaxis_title="Minimaler SOC",
            yaxis_title="Absolute Häufigkeit",
            margin=dict(l=20, r=20, t=40, b=20),
        )

        return html.Div(dcc.Graph(figure=fig), id=ids.MIN_SOC_HISTOGRAM)

    return html.Div(id=ids.MIN_SOC_HISTOGRAM)


def render_minimal_soc_per_rotation(app: Dash) -> html.Div:
    """
    Renders a histogram of minimal state of charge (SOC) for selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered minimal SOC histogram.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.ROT_SOC_HISTOGRAM, "children"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    @set_styling
    def update_soc_histogram(
        _, buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        # Adjust display settings
        pd.set_option("display.max_columns", None)  # Show all columns
        pd.set_option("display.expand_frame_repr", False)  # Prevent line wrapping

        soc_df = get_critical_rotations_and_score_as_dataframe(s.id, buses)

        min_soc_per_v_id = soc_df.groupby("R_id")["soc_end"].min().reset_index()

        # Set the desired bin width
        bin_width = 0.05  # Specify your desired bin width here

        # Calculate the number of bins based on the bin width
        max_soc = min_soc_per_v_id["soc_end"].max()
        min_soc = min_soc_per_v_id["soc_end"].min()
        num_bins = int((max_soc - min_soc) / bin_width)

        # Following line is needed due to plotly bug,
        # see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
        fig = go.Figure(layout=dict(template="plotly"))

        fig = px.histogram(
            min_soc_per_v_id,
            x="soc_end",
            title="Minimaler SOC pro Umlauf",
            nbins=num_bins,
            barmode="overlay",
            color_discrete_sequence=color_scheme,
        )

        # Update layout to display bars in front of each other
        fig.update_layout(
            barmode="overlay",
            showlegend=False,
            coloraxis_showscale=False,
            xaxis_title="Minimaler SOC",
            yaxis_title="Absolute Häufigkeit",
            margin=dict(l=20, r=20, t=40, b=20),
        )

        return html.Div(dcc.Graph(figure=fig), id=ids.ROT_SOC_HISTOGRAM)

    return html.Div(id=ids.ROT_SOC_HISTOGRAM)
