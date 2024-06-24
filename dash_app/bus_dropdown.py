import dash
from dash import Dash, html, dcc
from . import ids
from .data import get_all_buses_labeled
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
            Output(ids.BUS_DROPDOWN_RAW, "value"),
            Output(ids.BUS_DROPDOWN_RAW, "options"),
        ],
        [
            Input(ids.SELECT_NO_BUSES_BUTTON, "n_clicks"),
            Input(ids.SELECT_ALL_BUSES_BUTTON, "n_clicks"),
        ],
        prevent_initial_call=False
    )
    def update_bus_selection(no_buses_clicks, all_buses_clicks, session_state=None, dash_app=None, **kwargs):
        """
        Updates the dropdown menu based on which button is clicked.

        :param no_buses_clicks: The number of clicks on the "Select None" button.
        :param all_buses_clicks: The number of clicks on the "Select All" button.
        :param session_state: State of the session.
        :param dash_app: Dash application instance.
        :param **kwargs: Additional keyword arguments.

        :return: A tuple containing two elements:
            - The list of selected bus values.
            - The list of dictionaries representing the options for the dropdown i.e. bus short_names
        :rtype: tuple[list, list]
        """
        ctx = dash.callback_context
        button_id = ""
        if len(ctx.triggered) > 0:
            button_id = ctx.triggered[0]['prop_id'].split('.')[0]

        if button_id == ids.SELECT_NO_BUSES_BUTTON:
            return [], []
        else:
            task_id = dash_app.slug
            vehicle_name_dict, vehicle_name_dict_reverse = get_all_buses_labeled(task_id)
            return list(vehicle_name_dict.keys()), [
                {"label": bus_label, "value": bus_id}
                for bus_label, bus_id in vehicle_name_dict_reverse.items()
            ]

    return html.Div(
        children=[
            html.H6("Einen oder mehrere Bus(se) auswählen:"),
            dcc.Dropdown(
                # Id with which this dropdown can be called
                id=ids.BUS_DROPDOWN_RAW,
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
                className="dropdown-button",
                children=["Alle auswählen"],
                id=ids.SELECT_ALL_BUSES_BUTTON,
            ),
            # Create a button to select all buses
            html.Button(
                className="dropdown-button",
                children=["Alle abwählen"],
                id=ids.SELECT_NO_BUSES_BUTTON
            ),
            # Create a button to select all buses
            html.Button(
                className="apply-button", children=["Auswahl bestätigen"], id=ids.APPLY_DROPDOWN
            ),

        ],
    )
