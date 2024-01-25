from dash import Dash, html, dcc

from ebustoolbox.models import Scenario, Rotation
from . import ids
from dash.dependencies import Input, Output


def render(app: Dash) -> html.Div:
    @app.callback(
        [
            Output(ids.BUS_DROPDOWN, "value"),
            Output(ids.BUS_DROPDOWN, "options"),
            Output(ids.HIDDEN_DIV_ALL_BUSES, "value"),
        ],
        Input(ids.SELECT_ALL_BUSES_BUTTON, "n_clicks"),
        Input(ids.HIDDEN_DIV_FOR_SLUG, "children"),
    )
    def select_all_buses(_: int, task_id: str, session_state=None):
        all_buses = session_state.get(task_id, dict()).get("all_buses", None)
        if all_buses is None:
            s = Scenario.objects.get(task_id=task_id)
            rotations = Rotation.objects.filter(scenario=s)
            all_buses = [r.vehicle.name_short for r in rotations]
            try:
                session_state[task_id]
            except KeyError:
                session_state[task_id] = dict()
            session_state[task_id]["all_buses"] = all_buses
        return all_buses, [{"label": bus, "value": bus} for bus in all_buses], all_buses

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
