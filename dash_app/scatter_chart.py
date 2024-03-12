from dash import Dash, html, dcc
from . import ids
from dash.dependencies import Input, Output  # no fa401
import plotly.graph_objects as go
from . import data
import time
from ebustoolbox.models import Scenario, Rotation
import plotly.express as px
from plotly.subplots import make_subplots


def render(app: Dash) -> html.Div:
    """
    Renders a Div element containing a scatter chart showing SOC over time for selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the scatter chart.
    :rtype: html.Div
    """
    @app.callback(Output(ids.SCATTER_CHART, "figure"), Input(ids.BUS_DROPDOWN, "value"))
    def update_scatter(buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        start = time.time()
        df = data.get_soc_as_dataframe(s.id, buses)
        end = time.time()
        data.register_time("soc_scatter", start, end, "retrieval")

        start = time.time()
        # following line is needed due to plotly bug,
        # see https://stackoverflow.com/questions/74367104/dashboard-plotly-valueerror-invalid-value
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
    """
    Renders a Div element containing a line chart showing power draw over time by station ID for selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the line chart.
    :rtype: html.Div
    """
    @app.callback(Output(ids.POWER_DRAW_CHART, "figure"), Input(ids.BUS_DROPDOWN, "value"))
    def power_draw(buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        start = time.time()
        df = data.get_powerdraw_as_dataframe(s.id, buses)
        end = time.time()
        data.register_time("power_draw", start, end, "retrieval")

        # Create subplots
        fig = make_subplots(rows=len(df['Station_id'].unique()), cols=1, shared_xaxes=True,
                            subplot_titles=list(df['Station_id'].unique()))

        # Loop through each station
        for i, station_id in enumerate(df['Station_id'].unique()):
            station_df = df[df['Station_id'] == station_id]

            # Get unique colors for V_id
            colors = px.colors.qualitative.Plotly[:len(station_df['V_id'].unique())]

            # Loop through each V_id in the station
            for j, vehicle_id in enumerate(station_df['V_id'].unique()):
                vehicle_df = station_df[station_df['V_id'] == vehicle_id]
                fig.add_trace(go.Scatter(x=vehicle_df['time_start'], y=vehicle_df['Energy'],
                                         mode='lines',
                                         name=f'V_id: {vehicle_id}',
                                         line=dict(color=colors[j % len(colors)])),
                              row=i + 1, col=1)

        # Update layout
        fig.update_layout(title_text='Energy Consumption Over Time by Station ID',
                          xaxis_title='Time', yaxis_title='Energy',
                          showlegend=True)

        end = time.time()
        data.register_time("power_draw", start, end, "render")
        return fig

    return html.Div(dcc.Graph(id=ids.POWER_DRAW_CHART), style={"verticalAlign": "top"})
