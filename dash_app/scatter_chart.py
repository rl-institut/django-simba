from dash import Dash, html, dcc
from . import ids
from dash.dependencies import Input, Output  # no fa401
import plotly.graph_objects as go
from .colorscheme import color_scheme
from . import data
import time
from ebustoolbox.models import Scenario, Rotation
import plotly.express as px

def render(app: Dash) -> html.Div:
    @app.callback(Output(ids.SCATTER_CHART, "figure"), Input(ids.BUS_DROPDOWN, "value"))
    def update_scatter(buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        start = time.time()
        df = data.get_soc_as_dataframe(s.id, buses)
        end = time.time()
        data.register_time("soc_scatter", start, end, "retrieval")

        start = time.time()
        fig = go.Figure(layout=dict(template='plotly'))
        fig = px.line(df, x='time_end', y='soc_end', color='V_id',
                      title='Buses SOC over Time')
        fig.update_layout(
            margin=dict(l=20, r=20, t=20, b=20),
        )

        fig.update_layout(showlegend=False)

        end = time.time()
        data.register_time("soc_scatter", start, end, "render")

        return fig

    return html.Div(dcc.Graph(id=ids.SCATTER_CHART), style={"verticalAlign": "top"})


def render_power_draw(app: Dash) -> html.Div:
    @app.callback(Output(ids.POWER_DRAW_CHART, "figure"), Input(ids.BUS_DROPDOWN, "value"))
    def power_draw(buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug

        s = Scenario.objects.get(task_id=task_id)

        start = time.time()
        df = data.get_powerdraw_as_dataframe(s.id, buses)
        end = time.time()
        data.register_time("power_draw", start, end, "retrieval")

        start = time.time()
        fig = go.Figure(layout=dict(template='plotly'))
        fig = px.line(df, x='Time_start', y='Energy', color='Station_id',
                      title='Energy Consumption Over Time by Station ID',
                      labels={'Time_start': 'Time', 'Energy': 'Energy', 'Station_id': 'Station ID', 'V_id': 'Vehicle'},
                      line_group='Station_id')

        # Update layout
        fig.update_layout(xaxis_title='Time', yaxis_title='Energy')

        end = time.time()
        data.register_time("power_draw", start, end, "render")
        return fig
        #return html.Div(dcc.Graph(figure=fig), id=ids.POWER_DRAW_CHART)

    return html.Div(dcc.Graph(id=ids.POWER_DRAW_CHART), style={"verticalAlign": "top"})