from django.shortcuts import render
from django_plotly_dash import DjangoDash
from dash import dcc, html, Input, Output
import plotly.express as px
import plotly.graph_objects as go

from .util import get_soc
from ebustoolbox.models import VehicleProperties, Vehicle, Scenario

app = DjangoDash('MyDashApp')

# Dummy function to fetch data based on user ID
def fetch_data(user_id):
    # Replace this with your logic to fetch data based on user ID
    # For simplicity, just return some dummy data
    print("fetching: " + user_id + " of type " + str(type(user_id)))
    scenario = Scenario.objects.get(task_id=user_id)
    vehicles = Vehicle.objects.filter(vehicle_type__scenario=scenario)
    print(scenario.id, vehicles)
    vals = get_soc(scenario.id)

    data = {
        'user_id': user_id,
    }

    for vehicle in vehicles:
        key = f'vehicle_{vehicle.id}\n'
        values = vals[vehicle.id][1]['timeseries']
        data[key] = values

    return data

# Dummy function to create a Plotly figure
def create_figure(data):
    # Replace this with your logic to create a Plotly figure
    # For simplicity, just create a scatter plot
    print(data)

    u_id = data["user_id"]
    data.pop("user_id")



    # Use px.line to plot multiple lines dynamically
    #fig = px.line(df, x=df.index + 1, y=df.columns[1:], title=f'Data for User {data["user_id"]}')
    dict_of_fig = dict({
        "layout": {"legend": {"orientation": "h", "itemsizing": "constant", "itemwidth": 300, "tracegroupgap" : 50}}
    })

    fig = go.Figure(dict_of_fig)
    for v_name, y_values in data.items():
        x_values = list(range(1, len(y_values) + 1))
        fig.add_trace(go.Scatter(x=x_values, y=y_values, name=v_name, line=dict(width=4)))

    #fig.update_layout(title={"font_size": 22, "xanchor": "center", "x": 0.5})
    fig.update_layout(title=f'Data for User {u_id}')
    #fig.update_layout(legend=dict(font=dict(family="Courier", size=50, color="black")),
     #                 legend_title=dict(font=dict(family="Courier", size=30, color="blue")))

    return fig

app.layout = html.Div([
    dcc.Input(id='userid', type='hidden', value=''),
    dcc.Graph(id='my-graph', config={'displayModeBar': False}, style={'height': '700px'}),
],style={'width': '100%', 'overflow': 'visible', 'height': '700px'})

@app.callback(
    Output('my-graph', 'figure'),
    [Input('userid', 'value')]
)
def update_graph(value):
    # Fetch data based on the user ID
    print()
    print('IDDDDD: ' + str(value))
    data = fetch_data(value)
    print("Now new plot???")

    # Create and return the Plotly figure
    figure = create_figure(data)
    return figure

