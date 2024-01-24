from django.shortcuts import render
from django_plotly_dash import DjangoDash
from dash import dcc, html, Input, Output
import plotly.express as px
import plotly.graph_objects as go

from .util import get_soc
from ebustoolbox.models import VehicleProperties, Vehicle, Scenario

app = DjangoDash('MyDashApp')

def fetch_data(user_id):
    # Replace this with your logic to fetch data based on user ID
    # For simplicity, just return some dummy data
    scenario = Scenario.objects.get(task_id=user_id)
    vehicles = Vehicle.objects.filter(vehicle_type__scenario=scenario)
    vals = get_soc(scenario.id)

    data = {
        'user_id': user_id,
    }

    for vehicle in vehicles:
        key = f'vehicle_{vehicle.id}\n'
        values = vals[vehicle.id][1]['timeseries']
        data[key] = values

    return data

def create_line_chart(data):
    # Create a line chart using Plotly
    u_id = data["user_id"]
    data.pop("user_id")
    dict_of_fig = dict({
        "layout": {"legend": {"orientation": "v", "itemsizing": "constant", "itemwidth": 50, "tracegroupgap": 50}}
    })
    fig = go.Figure(dict_of_fig)
    for v_name, y_values in data.items():
        x_values = list(range(1, len(y_values) + 1))
        fig.add_trace(go.Line(x=x_values, y=y_values, name=v_name, line=dict(width=4)))

    fig.update_layout(title=f'Data for User {u_id}')

    return fig

def create_pie_chart(data):
    # Create a pie chart using Plotly Express
    fig = px.pie(names=list(data.keys()), values=list(map(len, data.values())), title=f'Pie Chart for User {data["user_id"]}')
    return fig

# Layout of the app
app.layout = html.Div([
    dcc.Input(id='userid', type='hidden', value=''),
    dcc.Graph(id='line-chart', config={'displayModeBar': False}, style={'height': '550px'}),
    dcc.Graph(id='pie-chart', config={'displayModeBar': False}, style={'height': '550px'}),
], style={'width': '100%', 'overflow': 'visible', 'height': '700px'})

# Callback to update the line chart based on user input
@app.callback(
    Output('line-chart', 'figure'),
    [Input('userid', 'value')]
)
def update_line_chart(value):
    # Fetch data based on the user ID
    data = fetch_data(value)

    # Create and return the line chart
    figure = create_line_chart(data)
    return figure

# Callback to update the pie chart based on user input
@app.callback(
    Output('pie-chart', 'figure'),
    [Input('userid', 'value')]
)
def update_pie_chart(value):
    # Fetch data based on the user ID
    data = fetch_data(value)

    # Create and return the pie chart
    figure = create_pie_chart(data)
    return figure