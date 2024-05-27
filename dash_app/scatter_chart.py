from dash import Dash, html, dcc
from . import ids
from dash.dependencies import Input, Output  # no fa401
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

    @app.callback(Output(ids.SCATTER_CHART, "figure"), Input(ids.BUS_DROPDOWN, "data"))
    def update_scatter(buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        df = data.get_soc_as_dataframe(s.id, buses)

        # following line is needed due to plotly bug,
        # see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
        fig = go.Figure(layout=dict(template="plotly"))
        df["soc_end_prc"] = df["soc_end"] * 100
        fig = px.line(df, x="time_end", y="soc_end_prc", color="V_id", hover_name="readable_name")
        fig.update_layout(
            margin=dict(l=20, r=20, t=40, b=20),
        )

        fig.update_layout(
            title_text="Bus SOC über Zeit",
            xaxis_title="Zeit",
            yaxis_title="SOC [%]",
            showlegend=False,
        )

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

    @app.callback(Output(ids.POWER_DRAW_CHART, "figure"), Input(ids.BUS_DROPDOWN, "data"))
    def power_draw(buses: list[str], session_state=None, dash_app=None, **kwargs):
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
                        name=f"Vehicle_id: {vehicle_id}",
                        line=dict(color=colors[j % len(colors)], shape="hv"),
                    ),
                    row=i + 1,
                    col=1,
                )

        # Update layout
        fig.update_layout(
            title_text="Ladeenergie über Zeit, pro Stations ID",
            xaxis_title="Zeit",
            yaxis_title="Energie [kW]",
            showlegend=False,
            margin=dict(l=20, r=20, t=40, b=20),
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

    @app.callback(Output(ids.STATION_OCCUPATION, "figure"), Input(ids.BUS_DROPDOWN, "data"))
    def occupation(buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        df = data.get_powerdraw_as_dataframe(s.id, buses)
        # Group by 'Station_id' and 'time_start', count unique 'V_id'

        fig = go.Figure(layout=dict(template="plotly"))
        if len(df["Station_id"].unique()) >= 1:
            # Get unique Station_ids
            df["time_start"] = pd.to_datetime(df["time_start"])
            df["time_end"] = pd.to_datetime(df["time_end"])

            # Create a DataFrame representing the charging status at different points in time
            charging_status = []

            # Generate all time points between the minimum and maximum time_start and time_end
            all_times = pd.date_range(
                start=df["time_start"].min(), end=df["time_end"].max(), freq="min"
            )

            for time_point in all_times:
                # Count the number of vehicles charging at this time point
                charging_vehicles = (
                        ((df["time_start"] <= time_point) & (df["time_end"] > time_point))
                        & (df["Power"] > 0)
                ).sum()
                charging_status.append({"time": time_point, "vehicles_charging": charging_vehicles})

            charging_status_df = pd.DataFrame(charging_status)
            fig = px.line(
                charging_status_df,
                x="time",
                y="vehicles_charging",
                title="Number of Vehicles Charging Over Time",
            )

            fig.update_layout(
                title_text="Anzahl der Ladenden Busse im Szenario",
                xaxis_title="Zeit",
                yaxis_title="Anzahl Busse",
                showlegend=False,
                margin=dict(l=20, r=20, t=40, b=20),
            )
        return fig

    return html.Div(dcc.Graph(id=ids.STATION_OCCUPATION), style={"verticalAlign": "top"})
