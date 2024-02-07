from dash import Dash, html, dcc
import plotly.express as px
from . import ids, data
from dash.dependencies import Input, Output
from ebustoolbox.models import Scenario, Rotation
import pandas as pd


def render(app: Dash) -> html.Div:
    @app.callback(
        Output(ids.BAR_CHART, "children"),
        Input(ids.BUS_DROPDOWN, "value"),
    )
    def update_timeline_chart(buses: list[str], session_state=None, dash_app=None, **kwargs) -> html.Div:
        print("updating bar chart")
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        df = data.get_activities_as_dataframe(s.id, buses)

        fig = px.timeline(
            df, x_start="time_start", x_end="time_end", y="V_id",
            hover_data=['event_type'],
            color='event_type'
        )

        return html.Div(dcc.Graph(figure=fig), id=ids.BAR_CHART)

    return html.Div(id=ids.BAR_CHART)
