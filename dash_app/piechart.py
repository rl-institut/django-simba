from dash import Dash, html, dcc
from . import ids
from dash.dependencies import Input, Output  # no fa401
from .data import get_df_perf
import plotly.graph_objects as go
from .colorscheme import color_scheme
import plotly.express as px
import pandas as pd
import time
def render_performance(app: Dash) -> html.Div:
    @app.callback(Output(ids.PIE_PERFORMANCE, "figure"), Input(ids.BUS_DROPDOWN, "value"))
    def update_scatter(buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        filter_dict = dict(task_id=task_id, vehicle__name_short__in=buses)

        time.sleep(10)

        # Sample data
        df = get_df_perf()

        print(df)

        # Create a pie chart
        sum_of_columns = df.sum()

        # Create the pie chart
        fig = px.pie(names=sum_of_columns.index, values=sum_of_columns.values, title='Performance')

        # Sample data
        return fig

    return html.Div(dcc.Graph(id=ids.PIE_PERFORMANCE), style={"verticalAlign": "top"})


def render_bustype(app: Dash) -> html.Div:
    @app.callback(Output(ids.PIE_BUSTYPE, "figure"), Input(ids.PIE_BUSTYPE, "value"))
    def update_scatter(buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        filter_dict = dict(task_id=task_id, vehicle__name_short__in=buses)

        # Sample data
        df = get_df_perf()

        # Create the pie chart
        fig = px.pie(df)

        return fig

    return html.Div(dcc.Graph(id=ids.PIE_BUSTYPE), style={"verticalAlign": "top"})