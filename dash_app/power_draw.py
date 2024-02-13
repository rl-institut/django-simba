from dash import Dash, html, dcc
import plotly.express as px
from . import ids
from dash.dependencies import Input, Output  # no fa401
from .data import get_scatter_plot_data
import plotly.graph_objects as go
from ebustoolbox.models import Scenario
from . import data

def render(app: Dash) -> html.Div:
    @app.callback(Output(ids.POWER_DRAW_CHART, "figure"), Input(ids.BUS_DROPDOWN, "value"))
    def power_draw(buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug

        s = Scenario.objects.get(task_id=task_id)

        df = data.get_powerdraw_as_dataframe(s.id, buses)
        fig = px.histogram(df, x='Time', y="Energy", color="Station_id")

        # Update layout to display bars in front of each other
        fig.update_layout(showlegend=False)
        fig.update_coloraxes(showscale=True)
        fig.update_xaxes(title_text='Energie in kWh')
        fig.update_yaxes(title_text='Zeit')

        return html.Div(dcc.Graph(figure=fig), id=ids.POWER_DRAW_CHART)

    return html.Div(dcc.Graph(id=ids.POWER_DRAW_CHART), style={"verticalAlign": "top"})
