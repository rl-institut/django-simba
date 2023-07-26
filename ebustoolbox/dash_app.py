
from django_plotly_dash import DjangoDash


from dash import dcc, html, Input, Output
import plotly.express as px

app = DjangoDash('SimpleExampleApp')   # replaces dash.Dash
app.layout = html.Div([
    # html.H4('Analysis of Iris data using scatter matrix'),
    dcc.Dropdown(
        id="dropdown",
        options=['sepal_length', 'sepal_width', 'petal_length', 'petal_width'],
        value=['sepal_length', 'sepal_width'],
        multi=True,
    ),
    dcc.Graph(id='graph',
              figure={
                  "layout": {
                      "height": 500,  # px
                  },
              }, )
])


@app.callback(
    Output("graph", "figure"),
    Input("dropdown", "value"))
def update_bar_chart(dims):
    df = px.data.iris()  # replace with your own data source
    fig = px.scatter_matrix(
        df, dimensions=dims, color="species")
    return fig
