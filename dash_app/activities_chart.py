from dash import Dash, html, dcc
import plotly.express as px
from . import ids, data
from dash.dependencies import Input, Output
from ebustoolbox.models import Scenario, Rotation
import pandas as pd
import time

df_perf = data.get_df_perf()
def render(app: Dash) -> html.Div:
    @app.callback(
        Output(ids.BAR_CHART, "children"),
        Input(ids.BUS_DROPDOWN, "value"),
    )
    def update_timeline_chart(buses: list[str], session_state=None, dash_app=None, **kwargs) -> html.Div:
        global df_perf
        start_time = time.time()

        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        df = data.get_activities_as_dataframe(s.id, buses)

        fig = px.timeline(
            df, x_start="time_start", x_end="time_end", y="V_id",
            hover_data=['event_type'],
            color='event_type'
        )

        end_time = time.time()
        elapsed_time = end_time - start_time
        data.register_time(0, 0, elapsed_time)

        return html.Div(dcc.Graph(figure=fig), id=ids.BAR_CHART)

    return html.Div(id=ids.BAR_CHART)
