from dash import Dash, html, dcc
from . import ids, data
from dash.dependencies import Input, Output  # no fa401
import plotly.graph_objects as go
from .colorscheme import color_scheme
import plotly.express as px
from ebustoolbox.models import Scenario, Rotation
import time
def render_critical_rotations(app: Dash) -> html.Div:
    @app.callback(Output(ids.PIE_CRITICAL, "figure"), Input(ids.BUS_DROPDOWN, "value"))
    def update_pie(buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        start = time.time()
        df = data.critical_rotations(s.id, buses)
        end = time.time()
        data.register_time("Pie critical", start, end, "retrieval")

        start = time.time()
        # Create a pie chart
        # Plotting the pie chart
        fig = px.pie(df, values='Count', names='Category', title='Counts of Critical and Non-Critical SOC Values',
                     hover_data={'URL': False})

        # Adding URLs as custom data
        fig.update_traces(customdata=df['URL'])

        # Update click behavior to open URLs
        fig.update_traces(hoverinfo='label+value',
                          textinfo='percent',
                          textposition='inside',
                          hovertemplate='%{label}: %{value} instances <br> Click to open URL')

        fig.update_layout(showlegend=True)

        end = time.time()
        data.register_time("Pie critical", start, end, "render")
        return fig

    return html.Div(dcc.Graph(id=ids.PIE_CRITICAL), style={"verticalAlign": "top"})


def render_bustype(app: Dash) -> html.Div:
    @app.callback(Output(ids.PIE_BUSTYPE, "figure"), Input(ids.BUS_DROPDOWN, "value"))
    def update_pie(buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        start = time.time()
        df = data.get_vehicle_types(s.id, buses)
        end = time.time()
        data.register_time("Pie Bustype", start, end, "retrieval")

        start = time.time()
        # Create a pie chart
        fig = go.Figure(layout=dict(template='plotly'))
        fig = px.pie(df, values='count', names='name', title='Vehicle Type Distribution')
        end = time.time()
        data.register_time("Pie Bustype", start, end, "render")
        return fig

    return html.Div(dcc.Graph(id=ids.PIE_BUSTYPE), style={"verticalAlign": "top"})