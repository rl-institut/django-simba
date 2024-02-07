from dash import Dash, html, dcc
import plotly.express as px
from . import ids
from dash.dependencies import Input, Output
from ebustoolbox.models import Scenario, Rotation
import pandas as pd
import numpy as np
from . import data

def render(app: Dash) -> html.Div:
    @app.callback(
        Output(ids.DUR_HISTOGRAM, "children"),
        Input(ids.BUS_DROPDOWN, "value"),
    )
    def update_distances_histogram(buses: list[str], session_state=None, dash_app=None, **kwargs) -> html.Div:
        print("updating SOC Histogram")
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        df = data.get_distances_as_dataframe(s.id, buses)

        # Set the desired bin width
        bin_width = 1000  # Specify your desired bin width here

        # Calculate the number of bins based on the bin width
        max_distance = df['total_distance'].max()
        min_distance = df['total_distance'].min()
        num_bins = int((max_distance - min_distance) / bin_width)


        fig = px.histogram(df, x='total_distance', color="V_id",nbins=num_bins, barmode='overlay')

        # Update layout to display bars in front of each other
        fig.update_layout(barmode='overlay')
        fig.update_layout(showlegend=False)
        fig.update_coloraxes(showscale=False)
        fig.update_xaxes(title_text='SOC')
        fig.update_yaxes(title_text='Relative Häufigkeit in %')

        return html.Div(dcc.Graph(figure=fig), id=ids.DUR_HISTOGRAM)

    return html.Div(id=ids.DUR_HISTOGRAM)
