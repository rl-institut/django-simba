from dash import Dash, html
from . import bus_dropdown, bar_chart, ids


def create_layout(app: Dash) -> html.Div:
    return html.Div(
        className="app-div",
        children=[
            html.Div(id=ids.HIDDEN_DIV_FOR_SLUG, style="None"),
            html.Div(id=ids.HIDDEN_DIV_ALL_BUSES, style="None"),
            html.H1(app.title),
            html.Hr(),
            html.Div(className="dropdown-contain", children=[bus_dropdown.render(app)]),
            bar_chart.render(app),
        ],
    )
