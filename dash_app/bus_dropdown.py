from dash import Dash, html, dcc

from . import ids
from .data import get_all_buses
from dash.dependencies import Input, Output


def render(app: Dash) -> html.Div:
    @app.callback(
        [
            Output(ids.BUS_DROPDOWN, "value"),
            Output(ids.BUS_DROPDOWN, "options"),
        ],
        Input(ids.SELECT_ALL_BUSES_BUTTON, "n_clicks"),
    )
    def select_all_buses(_: int, session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        all_buses = session_state.get(task_id, {}).get(
            "all_buses", get_all_buses(session_state, task_id)
        )
        try:
            session_state[task_id]
        except KeyError:
            session_state[task_id] = dict()
        session_state[task_id]["all_buses"] = all_buses
        return all_buses, [{"label": bus, "value": bus} for bus in all_buses]

    return html.Div(
        children=[
            html.H6("Bus"),
            dcc.Dropdown(
                # Id with which this dropdown can be called
                id=ids.BUS_DROPDOWN,
                # Options are set by first select_all_buses call at page_load
                options=[{"label": bus, "value": bus} for bus in []],
                # initial Value
                # Value is set by first select_all_buses call at page_load
                value=[],
                # Are multiple options possible
                multi=True,
            ),
            # Create a button to select all buses
            html.Button(
                className="dropdown-button", children=["Select All"], id=ids.SELECT_ALL_BUSES_BUTTON
            ),
        ],
    )
