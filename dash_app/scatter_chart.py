from dash import Dash, html, dcc
from . import ids
from dash.dependencies import Input, Output, State  # no fa401
import plotly.graph_objects as go
from . import data
from ebustoolbox.models import Scenario
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd


def render(app: Dash) -> html.Div:
    """
    Renders a Div element containing a scatter chart showing SOC over time for selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the scatter chart.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.SCATTER_CHART, "figure"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "value"),
    )
    def update_scatter(_, buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        df = data.get_soc_as_dataframe(s.id, buses)

        # following line is needed due to plotly bug,
        # see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
        fig = go.Figure(layout=dict(template="plotly"))
        fig = px.line(df, x="time_end", y="soc_end", color="V_id", title="Buses SOC over Time")
        fig.update_layout(
            margin=dict(l=20, r=20, t=20, b=20),
        )

        fig.update_layout(showlegend=False)

        return fig

    return html.Div(dcc.Graph(id=ids.SCATTER_CHART), style={"verticalAlign": "top"})


def render_power_draw(app: Dash) -> html.Div:
    """
    Renders a Div element containing a line chart showing power draw over time by station ID for selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the line chart.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.POWER_DRAW_CHART, "figure"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "value"),
    )
    def power_draw(_, buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        df = data.get_powerdraw_as_dataframe(s.id, buses)

        fig = go.Figure(layout=dict(template="plotly"))

        if len(df["Station_id"].unique()) < 1:
            # Nothing to plot -> return empty figure
            return fig

        # Create subplots
        fig = make_subplots(
            rows=len(df["Station_id"].unique()),
            cols=1,
            shared_xaxes=True,
            subplot_titles=list(df["Station_id"].unique()),
        )

        # Loop through each station
        for i, station_id in enumerate(df["Station_id"].unique()):
            station_df = df[df["Station_id"] == station_id]

            # Get unique colors for V_id
            colors = px.colors.qualitative.Plotly[: len(station_df["V_id"].unique())]

            # Loop through each V_id in the station
            for j, vehicle_id in enumerate(station_df["V_id"].unique()):
                vehicle_df = station_df[station_df["V_id"] == vehicle_id]
                fig.add_trace(
                    go.Scatter(
                        x=vehicle_df["time_start"],
                        y=vehicle_df["Power"],
                        mode="lines",
                        name=f"V_id: {vehicle_id}",
                        line=dict(color=colors[j % len(colors)], shape="hv"),
                    ),
                    row=i + 1,
                    col=1,
                )

        # Update layout
        fig.update_layout(
            title_text="Charging Power over Time by Station ID",
            xaxis_title="Time",
            yaxis_title="Power [kW]",
            showlegend=True,
        )
        return fig

    return html.Div(dcc.Graph(id=ids.POWER_DRAW_CHART), style={"verticalAlign": "top"})


def render_station_occupation(app: Dash) -> html.Div:
    """
    Renders a Div element containing a line chart showing power draw over time by station ID for selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the line chart.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.STATION_OCCUPATION, "figure"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "value"),
    )
    def occupation(_, buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        df = data.get_powerdraw_as_dataframe(s.id, buses)

        # Group by 'Station_id' and 'time_start', count unique 'V_id'
        grouped = df.groupby("Station_id")

        fig = go.Figure(layout=dict(template="plotly"))
        if len(df["Station_id"].unique()) >= 1:
            # Get unique Station_ids
            df["time_start"] = pd.to_datetime(df["time_start"])

            # Group by 'Station_id' and 'time_start', then count unique 'V_id' values
            grouped = (
                df.groupby(["Station_id", df["time_start"].dt.date])["V_id"].nunique().reset_index()
            )
            # Plot using Plotly Express
            fig = px.line(
                grouped,
                x="time_start",
                y="V_id",
                color="Station_id",
                markers=True,
                labels={"time_start": "Date", "V_id": "Number of different V_ids charging"},
                title="Number of V_ids charging at different Stations over time",
            )
            fig.add_annotation(
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                text="Warning: Plot not working",
                font=dict(size=80),
                showarrow=False,
            )
        return fig

    return html.Div(dcc.Graph(id=ids.STATION_OCCUPATION), style={"verticalAlign": "top"})
