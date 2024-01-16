from dash import Dash, html, dcc
from . import ids
from dash.dependencies import Input, Output


def render(app: Dash) -> html.Div:
    all_buses = []

    @app.callback(
        Output(ids.BUS_DROPDOWN, "value"),
        Input(ids.SELECT_ALL_BUSES_BUTTON, "n_clicks"),
        Input(ids.HIDDEN_DIV_ALL_BUSES, "children"),
    )
    def select_all_buses(_: int, all_buses) -> list[str]:
        return all_buses

    return html.Div(
        children=[
            html.H6("Bus"),
            dcc.Dropdown(
                # Id with which this dropdown can be called
                id=ids.BUS_DROPDOWN,
                options=[{"label": bus, "value": bus} for bus in all_buses],
                # initial Value
                value=all_buses,
                # Are multiple options possible
                multi=True,
            ),
            # Create a button to select all buses
            html.Button(
                className="dropdown-button", children=["Select All"], id=ids.SELECT_ALL_BUSES_BUTTON
            ),
        ],
    )
