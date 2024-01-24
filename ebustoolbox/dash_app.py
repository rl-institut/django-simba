from django.shortcuts import render
from django_plotly_dash import DjangoDash
from dash import dcc, html, Input, Output
import plotly.express as px
import plotly.graph_objects as go

from .util import get_soc, get_soc_as_dataframe
from ebustoolbox.models import VehicleProperties, Vehicle, Scenario
import random

app = DjangoDash('MyDashApp')

def fetch_data_lines(user_id):
    # Replace this with your logic to fetch data based on user ID
    # For simplicity, just return some dummy data
    scenario = Scenario.objects.get(task_id=user_id)
    vehicles = Vehicle.objects.filter(vehicle_type__scenario=scenario)
    df = get_soc_as_dataframe(scenario.id)

    return user_id, df

def generate_random_data(num_entries=5):
    data = {}

    for i in range(num_entries):
        key = f'Category {i+1}'
        value = random.uniform(0, 1)
        data[key] = value

    return data

def create_line_chart(user_id, result_df):
    # Create a line chart using Plotly

    dict_of_fig = dict({
        "layout": {"legend": {"orientation": "v", "itemsizing": "constant", "itemwidth": 50, "tracegroupgap": 50}}
    })

    fig = go.Figure(dict_of_fig)
    for v_id, group in result_df.groupby('V_id'):
        x_values = group['Time']
        y_values = group['SOC']
        fig.add_trace(go.Scatter(x=x_values, y=y_values, mode='lines', name=f'V_id {v_id}', line=dict(width=4)))

    # Update layout for better visualization
    fig.update_layout(title='SOC Over Time',
                      xaxis_title='Time',
                      yaxis_title='SOC')

    fig.update_layout(title=f'Data for User {user_id}')

    return fig

def create_pie_chart(data):
    # Create a pie chart using Plotly Express
    fig = px.pie(names=list(data.keys()), values=list(data.values()), title='Random Pie Chart')
    return fig

# Layout of the app
app.layout = html.Div([
    dcc.Input(id='userid', type='hidden', value=''),
    dcc.Graph(id='line-chart', config={'displayModeBar': False}, style={'height': '550px'}),
    dcc.Graph(id='pie-chart', config={'displayModeBar': False}, style={'height': '550px'}),
], style={'display': 'block', ' width': '80%', 'overflow': 'visible', 'height': '700px'})

# Callback to update the line chart based on user input
@app.callback(
    Output('line-chart', 'figure'),
    [Input('userid', 'value')]
)
def update_line_chart(value):
    # Fetch data based on the user ID
    id, data = fetch_data_lines(value)
    # Create and return the line chart
    figure = create_line_chart(id, data)
    return figure

# Callback to update the pie chart based on user input
@app.callback(
    Output('pie-chart', 'figure'),
    [Input('userid', 'value')]
)
def update_pie_chart(value):
    # Generate random data
    random_data = generate_random_data()

    # Create a pie chart for the generated data
    pie_chart = create_pie_chart(random_data)

    return pie_chart