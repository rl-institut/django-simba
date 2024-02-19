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
        Output(ids.SOC_HISTOGRAM, "children"),
        Input(ids.BUS_DROPDOWN, "value"),
    )
    def update_soc_histogram(buses: list[str], session_state=None, dash_app=None, **kwargs) -> html.Div:
        print("updating SOC Histogram")
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        soc_df = data.get_soc_as_dataframe(s.id, buses)

        # Calculate alpha value
        alpha = 1 / len(soc_df['V_id'].unique())

        # Binning SOCs per vehicle in 0.05 bins
        bins = []
        for vehicle_id, group in soc_df.groupby('V_id'):
            hist, bin_edges = np.histogram(group['SOC'], bins=np.arange(-0.1, 1.1, 0.1), density=True)
            for i, b in enumerate(hist):
                bins.append({'V_id': vehicle_id, 'Bin': bin_edges[i], 'Frequency': b})

        # Normalize such that the sum of heights of all bins equals 100% for each vehicle
        normalized_bins = []
        for vehicle_id, group in pd.DataFrame(bins).groupby('V_id'):
            total_frequency = group['Frequency'].sum()
            for index, row in group.iterrows():
                normalized_bins.append(
                    {'V_id': vehicle_id, 'Bin': row['Bin'], 'Frequency': row['Frequency'] / total_frequency * 100})

        # Create a DataFrame for the normalized bins
        normalized_df = pd.DataFrame(normalized_bins)

        # Create a figure for the histogram
        fig = px.histogram(normalized_df, x='Bin', y='Frequency', color="V_id", barmode='overlay', opacity=alpha,
                     color_discrete_sequence=color_scheme)

        # Update layout to display bars in front of each other
        fig.update_layout(barmode='overlay')
        fig.update_layout(showlegend=False)
        fig.update_coloraxes(showscale=False)
        fig.update_xaxes(title_text='SOC')
        fig.update_yaxes(title_text='Relative Häufigkeit in %')

        return html.Div(dcc.Graph(figure=fig), id=ids.SOC_HISTOGRAM)

    return html.Div(id=ids.SOC_HISTOGRAM)
