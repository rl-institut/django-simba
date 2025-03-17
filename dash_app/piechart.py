from dash import Dash, html, dcc
from dash.exceptions import PreventUpdate

from . import ids, data
from dash.dependencies import Input, Output, State  # no fa401
import plotly.graph_objects as go
import plotly.express as px
from ebustoolbox.models import Scenario
from .style import set_styling


def render_critical_rotations(app: Dash) -> html.Div:
    """
    Renders a pie chart showing the counts of critical and non-critical state of charge (SOC) values for selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered pie chart.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.PIE_CRITICAL, "figure"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    @set_styling
    def update_pie(_, buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        if not data.sim_is_finished(task_id):
            return html.Div(go.Figure(layout=dict(template="plotly")))

        df = data.get_critical_rotations_as_dataframe(s.id, buses)

        # Create a pie chart following line is needed due to plotly bug,
        # see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
        fig = go.Figure(layout=dict(template="plotly"))
        fig = px.pie(
            df,
            values="Count",
            names="Category",
            title="Verteilung der kritischen und unkritischen Umläufe",
        )

        return fig

    return html.Div(dcc.Graph(id=ids.PIE_CRITICAL), style={"verticalAlign": "top"})


def render_bustype(app: Dash) -> html.Div:
    """
    Renders a pie chart showing the distribution of vehicle types for selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered pie chart.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.PIE_BUSTYPE, "figure"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    @set_styling
    def update_pie(_, buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        if not data.sim_is_finished(task_id):
            return html.Div(go.Figure(layout=dict(template="plotly")))

        df = data.get_vehicle_types(s.id, buses)
        if len(df) == 0:
            raise PreventUpdate

        # Create a pie chart following line is needed due to plotly bug,
        # see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
        fig = px.pie(df, values="count", names="name", title="Zusammensetzung der Fahrzeugtypen")
        return fig

    return html.Div(dcc.Graph(id=ids.PIE_BUSTYPE), style={"verticalAlign": "top"})


import json


def get_critical_rotations(buses: list[str], task_id: str) -> str:
    """
    Returns JSON data for ECharts to visualize the counts of critical and non-critical state of charge (SOC) values for selected buses.

    :param buses: List of buses selected.
    :param task_id: Task ID of the simulation.
    :return: JSON string containing the chart data in ECharts format.
    """

    # Simulate getting scenario and data (replace this with your actual data access logic)
    s = Scenario.objects.get(task_id=task_id)

    if not data.sim_is_finished(task_id):
        return json.dumps({})  # Empty JSON if simulation is not finished

    df = data.get_critical_rotations_as_dataframe(s.id, buses)

    # Prepare data in ECharts format
    chart_data = {
        "title": {
            "text": "Verteilung der kritischen und unkritischen Umläufe",
            "left": "center"
        },
        "series": [{
            "name": "Critical Rotations",
            "type": "pie",
            "radius": "50%",
            "data": [
                {"value": row["Count"], "name": row["Category"]}
                for _, row in df.iterrows()
            ]
        }]
    }

    return json.dumps(chart_data)


def get_bustype(buses: list[str], task_id: str) -> str:
    """
    Returns JSON data for ECharts to visualize the distribution of vehicle types for selected buses.

    :param buses: List of buses selected.
    :param task_id: Task ID of the simulation.
    :return: JSON string containing the chart data in ECharts format.
    """

    # Simulate getting scenario and data (replace this with your actual data access logic)
    s = Scenario.objects.get(task_id=task_id)

    if not data.sim_is_finished(task_id):
        return json.dumps({})  # Empty JSON if simulation is not finished

    df = data.get_vehicle_types(s.id, buses)
    if len(df) == 0:
        return json.dumps({})  # Return empty JSON if there's no data

    # Prepare data in ECharts format
    chart_data = {
        "title": {
            "text": "Zusammensetzung der Fahrzeugtypen",
            "left": "center"
        },
        "series": [{
            "name": "Vehicle Types",
            "type": "pie",
            "radius": "50%",
            "data": [
                {"value": row["count"], "name": row["name"]}
                for _, row in df.iterrows()
            ]
        }]
    }

    return json.dumps(chart_data)