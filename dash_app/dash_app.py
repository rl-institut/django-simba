from django_plotly_dash import DjangoDash
from .dash_layout import create_layout
from dash_bootstrap_components.themes import BOOTSTRAP

app = DjangoDash(
    "SimpleExampleApp", add_bootstrap_links=True, external_stylesheets=[BOOTSTRAP]
)  # replaces dash.Dash
app.title = "Ebus2030 dashboard"
app.layout = create_layout(app)
