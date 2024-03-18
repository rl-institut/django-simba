from dash import Dash, html, dcc
from dash.dependencies import Input, Output
from django.template.loader import render_to_string
from . import ids
import plotly.io as pio
import os
from ebustoolbox.models import Scenario
from . import data
import pandas as pd
def export(app: Dash) -> html.Div:
    @app.callback(
        Output('output', 'children'),
        [Input(ids.EXPORT_BUTTON, 'n_clicks'),
         Input(ids.BUS_DROPDOWN, "value")]
    )
    def update_output(n_clicks,buses: list[str], session_state=None, dash_app=None, **kwargs):
        if n_clicks:
            print("Button clicked!", buses)
            # Create a directory to save the plots if it doesn't exist
            save_dir = 'saved_plots'
            if not os.path.exists(save_dir):
                os.makedirs(save_dir)

            task_id = dash_app.slug
            s = Scenario.objects.get(task_id=task_id)

            df1 = data.get_soc_as_dataframe(s.id, buses)
            df2 = data.get_duration_as_dataframe(s.id, buses)
            df3 = data.get_activities_as_dataframe(s.id, buses)

            df1['time_start'] = df1['time_start'].dt.tz_localize(None)
            df3['time_start'] = df3['time_start'].dt.tz_localize(None)
            df1['time_end'] = df1['time_end'].dt.tz_localize(None)
            df3['time_end'] = df3['time_end'].dt.tz_localize(None)

            # Create a Pandas Excel writer using xlsxwriter as the engine
            writer = pd.ExcelWriter('output.xlsx', engine='xlsxwriter')

            # Write each dataframe to a separate worksheet
            df1.to_excel(writer, sheet_name='Sheet1', index=False)
            df2.to_excel(writer, sheet_name='Sheet2', index=False)
            df3.to_excel(writer, sheet_name='Sheet3', index=False)

            # Close the Pandas Excel writer and output the Excel file
            writer.close()

            return "SAVED!"
        else:
            return ""

    return html.Div([
        html.P("Some accompanying text"),
        html.Button('Export to Excel', id=ids.EXPORT_BUTTON),
        html.Div(id='output')
    ])
