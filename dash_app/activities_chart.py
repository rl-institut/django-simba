from dash import Dash, html, dcc
import plotly.express as px
from . import ids, data
from dash.dependencies import Input, Output
from ebustoolbox.models import Scenario, Rotation
import plotly.graph_objects as go
import time

def render(app: Dash) -> html.Div:
    @app.callback(
        Output(ids.BAR_CHART, "children"),
        Input(ids.BUS_DROPDOWN, "value"),
    )
    def update_timeline_chart(buses: list[str], session_state=None, dash_app=None, **kwargs) -> html.Div:

        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        start = time.time()
        df = data.get_activities_as_dataframe(s.id, buses)
        end = time.time()
        data.register_time("activities", start, end, "retrieval")

        start = time.time()
        # following line is needed due to plotly bug, see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
        fig = go.Figure(layout=dict(template='plotly'))
        fig = px.timeline(
            df, x_start="time_start", x_end="time_end", y="V_id",
            hover_data='event_type',
            color='event_type',
            height= 800
        )
        end = time.time()
        data.register_time("activities", start, end, "render")

        return html.Div(dcc.Graph(figure=fig), id=ids.BAR_CHART)

    return html.Div(id=ids.BAR_CHART)


def render_performance(app: Dash) -> html.Div:
    @app.callback(
        Output(ids.ACTIVITY_PERFORMANCE, "children"),
        Input(ids.BUS_DROPDOWN, "value"),
    )
    def update(buses: list[str], session_state=None, dash_app=None, **kwargs) -> html.Div:
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        time.sleep(5)

        # Sample data
        df = data.get_df_perf()
        fig = go.Figure(layout=dict(template='plotly'))
        fig = px.timeline(df, x_start='start', x_end='end', y='name', color='process')
        fig.update_layout(title='Process Timeline', xaxis_title='Time', yaxis_title='Function')

        return html.Div(dcc.Graph(figure=fig), id=ids.ACTIVITY_PERFORMANCE)

    return html.Div(id=ids.ACTIVITY_PERFORMANCE)