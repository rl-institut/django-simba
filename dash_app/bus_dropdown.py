from dash import Dash, html, dcc
from . import ids
from .data import get_all_buses
from dash.dependencies import Input, Output


def render(app: Dash) -> html.Div:
    """
    Renders a Dash app with a dropdown menu for selecting buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered dropdown menu.
    :rtype: html.Div
    """
    @app.callback(
        [
            Output(ids.BUS_DROPDOWN, "value"),
            Output(ids.BUS_DROPDOWN, "options"),
        ],
        Input(ids.SELECT_ALL_BUSES_BUTTON, "n_clicks"),
    )
    def select_all_buses(_: int, session_state=None, dash_app=None, **kwargs):
        """
        Selects all buses when the "Select All" button is clicked and updates the dropdown menu accordingly.

        :param _: The number of clicks on the "Select All" button (unused).
        :param session_state: State of the session.
        :param dash_app: Dash application instance.
        :param **kwargs: Additional keyword arguments.

        :return: A tuple containing two elements:
            - The list of all buses.
            - The list of dictionaries representing the options for the dropdown i.e. bus short_names
        :rtype: tuple[list, list]
        """
        task_id = dash_app.slug
        all_buses = session_state.get(task_id, {}).get("all_buses", get_all_buses(task_id))
        try:
            session_state[task_id]
        except KeyError:
            session_state[task_id] = dict()
        session_state[task_id]["all_buses"] = all_buses
        return all_buses, [{"label": bus, "value": bus} for bus in all_buses]

    return html.Div(
        children=[
            html.H6("Select one or multiple Buses:"),
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
