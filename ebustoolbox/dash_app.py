from django.shortcuts import render
from django_plotly_dash import DjangoDash
from dash import dcc, html, Input, Output
import plotly.express as px

app = DjangoDash('MyDashApp')

# Dummy function to fetch data based on user ID
def fetch_data(user_id):
    # Replace this with your logic to fetch data based on user ID
    # For simplicity, just return some dummy data
    print("fetching: " + user_id + " of type " + str(type(user_id)))
    if user_id == "123":
        print("MATCH!!")
        vals = [5,4,3,2,1,-9]
    else:
        vals = [1,2,3,4,5]
    return {'user_id': user_id, 'values': vals}

# Dummy function to create a Plotly figure
def create_figure(data):
    # Replace this with your logic to create a Plotly figure
    # For simplicity, just create a scatter plot
    fig = px.scatter(x=data['values'], y=data['values'], title=f'Data for User {data["user_id"]}')
    return fig

app.layout = html.Div([
    dcc.Input(id='userid', type='text', value=''),
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

