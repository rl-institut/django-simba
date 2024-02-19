from dash import Dash, html, dcc
import plotly.express as px
from . import ids
from dash.dependencies import Input, Output
from ebustoolbox.models import Scenario, Rotation
import pandas as pd
import numpy as np
from . import data
from .colorscheme import color_scheme

def render(app: Dash) -> html.Div:
    @app.callback(
        Output(ids.DIST_DUR_HISTOGRAM, "children"),
        Input(ids.BUS_DROPDOWN, "value"),
    )
    def dist_dur_histogram(buses: list[str], session_state=None, dash_app=None, **kwargs) -> html.Div:
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        dur_df = data.get_duration_as_dataframe(s.id, buses)
        dist_df = data.get_distances_as_dataframe(s.id, buses)

        # Set the desired bin width
        bin_width = 0.5  # Specify your desired bin width here

        dur_df["dist_per_dur"] = dist_df['total_distance']/dur_df['duration']

        # Calculate the number of bins based on the bin width
        max_distance = dur_df["dist_per_dur"].max()
        min_distance = dur_df["dist_per_dur"].min()

        num_bins = int((max_distance - min_distance) / bin_width)

        fig = px.histogram(dur_df, x='dist_per_dur', barmode='overlay', color_discrete_sequence=color_scheme)

        # Update layout to display bars in front of each other
        fig.update_layout(barmode='overlay')
        fig.update_layout(showlegend=False)
        fig.update_coloraxes(showscale=False)
        fig.update_xaxes(title_text='Durchschnittsgeschwindigkeit für Umlauf (inkl. Stillstand, Pausen) in m/s')
        fig.update_yaxes(title_text='Relative Häufigkeit in %')

        return html.Div(dcc.Graph(figure=fig), id=ids.DIST_DUR_HISTOGRAM)

    return html.Div(id=ids.DIST_DUR_HISTOGRAM)
