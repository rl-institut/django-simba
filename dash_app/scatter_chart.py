from dash import Dash, html, dcc
from . import ids
from dash.dependencies import Input, Output  # no fa401
import plotly.graph_objects as go
from .colorscheme import color_scheme
from . import data
import time

def render(app: Dash) -> html.Div:
    @app.callback(Output(ids.SCATTER_CHART, "figure"), Input(ids.BUS_DROPDOWN, "value"))
    def update_scatter(buses: list[str], session_state=None, dash_app=None, **kwargs):


        task_id = dash_app.slug
        filter_dict = dict(task_id=task_id, vehicle__name_short__in=buses)

        start = time.time()
        # Get the data
        datas = data.get_scatter_plot_data(filter_dict)
        end = time.time()
        data.register_time("soc_scatter", start, end, "retrieval")

        start = time.time()
        fig = go.Figure()
        for vehicle_key, value in datas.items():
            times, socs = value[0], value[1]
            fig.add_trace(go.Scatter(x=times, y=socs, name=vehicle_key, line=dict(width=4)))
        fig.update_layout(
            margin=dict(l=20, r=20, t=20, b=20),
        )

        fig.update_layout(showlegend=False)

        end = time.time()
        data.register_time("soc_scatter", start, end, "render")

        return fig

    return html.Div(dcc.Graph(id=ids.SCATTER_CHART), style={"verticalAlign": "top"})
