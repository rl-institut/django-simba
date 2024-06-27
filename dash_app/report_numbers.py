from dash import Dash, html, dash_table
from . import ids, data
from dash.dependencies import Input, Output, State  # no fa401
from .data import (
    get_number_longest_rot,
    get_number_shortest_rot,
    get_number_of_buses,
    get_critical_rotations_and_score_as_dataframe,
    get_distances_as_dataframe,
    get_total_consumption,
    get_number_of_stations,
)
from ebustoolbox.models import Scenario


def render_longest_rotation(app: Dash) -> html.Div:
    """
    Renders a Div element displaying the number of the longest rotation for selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered number of the longest rotation.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.NUMBER_LONGEST_ROTATION, "children"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    def update_report_numbers(
            _, buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        filter_dict = dict(task_id=task_id, vehicle__id__in=buses)

        # Get the data
        lines = get_number_longest_rot(filter_dict)

        styles = [
            {"fontFamily": "Helvetica", "fontSize": "25px", "position": "relative", "top": "0", "left": "0"},
            {"fontFamily": "Helvetica", "fontSize": "48px", "textAlign": "center"},
        ]

        html_div = []

        for line, style in zip(lines, styles):
            html_div.append(html.H2(line, style=style))
        number_divs = html.Div(html_div)
        return html.Div(number_divs, id=ids.NUMBER_LONGEST_ROTATION)

    return html.Div(id=ids.NUMBER_LONGEST_ROTATION)


def render_shortest_rotation(app: Dash) -> html.Div:
    """
    Renders a Div element displaying the number of the shortest rotation for selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered number of the shortest rotation.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.NUMBER_SHORTEST_ROTATION, "children"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    def update_report_numbers(
            _, buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        filter_dict = dict(task_id=task_id, vehicle__id__in=buses)

        # Get the data
        lines = get_number_shortest_rot(filter_dict)

        styles = [
            {"fontFamily": "Helvetica","fontSize": "25px", "position": "relative", "top": "0", "left": "0"},
            {"fontFamily": "Helvetica", "fontSize": "48px", "textAlign": "center"},
        ]

        html_div = []

        for line, style in zip(lines, styles):
            html_div.append(html.H2(line, style=style))
        number_divs = html.Div(html_div)
        return html.Div(number_divs, id=ids.NUMBER_SHORTEST_ROTATION)

    return html.Div(id=ids.NUMBER_SHORTEST_ROTATION)


def render_number_of_buses(app: Dash) -> html.Div:
    """
    Renders a Div element displaying the number of selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered number of selected buses.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.NUMBER_OF_BUSES, "children"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    def update_report_numbers(
            _, buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        filter_dict = dict(task_id=task_id, vehicle__id__in=buses)

        # Get the data
        lines = get_number_of_buses(filter_dict)

        styles = [
            {"fontFamily": "Helvetica", "fontSize": "25px", "position": "relative", "top": "0", "left": "0"},
            {"fontFamily": "Helvetica", "fontSize": "48px", "textAlign": "center"},
        ]

        html_div = []

        for line, style in zip(lines, styles):
            html_div.append(html.H2(line, style=style))
        number_divs = html.Div(html_div)
        return html.Div(number_divs, id=ids.NUMBER_OF_BUSES)

    return html.Div(id=ids.NUMBER_OF_BUSES)


def critical_rotations(app: Dash) -> html.Div:
    """
    Renders a Div element displaying the number of selected buses.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered number of selected buses.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.LIST_CRIT_ROTATIONS, "children"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    def update_report_numbers(
            _, buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        # Get the data
        lines = get_critical_rotations_and_score_as_dataframe(s.id, buses)
        df_sorted = lines.sort_values(by="soc_end")
        df_sorted["soc_end"] = df_sorted["soc_end"].round(3)
        # Generate HTML table dynamically
        # Generate HTML table dynamically
        table = html.Div(
            [
                html.Div(style={"height": "50px"}),  # Spacing div before table
                dash_table.DataTable(
                    id="table",
                    columns=[
                        {"name": "Rotations id", "id": "R_id"},
                        {"name": "Fahrzeug id", "id": "V_id"},
                        {"name": "Minimaler SOC", "id": "soc_end"},
                    ],
                    data=df_sorted.to_dict("records"),
                    page_size=10,  # set the maximum number of rows per page
                    style_cell={'font-family': 'Helvetica'},  # set font to Helvetica
                ),
                html.Div(style={"height": "75px"}),  # Spacing div after table
            ],
            style={'font-family': 'Helvetica'}  # set font to Helvetica
        )

        return table

    return html.Div(id=ids.LIST_CRIT_ROTATIONS)


def render_total_distance(app: Dash) -> html.Div:
    """
    Renders a Div element displaying the total driven distance.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered total driven distance.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.NUMBER_TOTAL_DIST, "children"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    def update_total_distance(
            _, buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        # print("updating numbers")
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        # Get the data
        dist_df = get_distances_as_dataframe(s.id, buses)

        lines = [
            "Gesamtstrecke:",
            str(round(dist_df["total_distance"].sum() / 1000, 3)) + " km",
        ]

        styles = [
            {"fontFamily": "Helvetica", "fontSize": "25px", "position": "relative", "top": "0", "left": "0"},
            {"fontFamily": "Helvetica", "fontSize": "48px", "textAlign": "center"},
        ]

        html_div = []

        for line, style in zip(lines, styles):
            html_div.append(html.H2(line, style=style))
        number_divs = html.Div(html_div)
        return html.Div(number_divs, id=ids.NUMBER_TOTAL_DIST)

    return html.Div(id=ids.NUMBER_TOTAL_DIST)


def render_avg_consumption(app: Dash) -> html.Div:
    """
    Renders a Div element displaying the average energy consumption.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered average energy consumption.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.NUMBER_AVG_CONSUM, "children"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    def update_avg_consumption(
            _, buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        # print("updating numbers")
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        # Get the data
        total_consumption = get_total_consumption(s)
        vehicle_name_dict, vehicle_name_dict_reverse = data.get_all_buses_labeled(task_id)
        buses = list(vehicle_name_dict.keys())
        dist_df = get_distances_as_dataframe(s.id, buses)

        lines = [
            "Durchschnittlicher Energieverbrauch:",
            str(round(total_consumption / (dist_df["total_distance"].sum() / 1000), 3)) + " kWh/km",
        ]

        styles = [
            {"fontFamily": "Helvetica", "fontSize": "25px", "position": "relative", "top": "0", "left": "0"},
            {"fontFamily": "Helvetica", "fontSize": "48px", "textAlign": "center"},
        ]

        html_div = []

        for line, style in zip(lines, styles):
            html_div.append(html.H2(line, style=style))
        number_divs = html.Div(html_div)
        return html.Div(number_divs, id=ids.NUMBER_AVG_CONSUM)

    return html.Div(id=ids.NUMBER_AVG_CONSUM)


def render_number_stations(app: Dash) -> html.Div:
    """
    Renders a Div element displaying the number of (electrified) stations.

    :param app: The Dash application instance.
    :type app: Dash

    :return: A Div element containing the rendered number of stations.
    :rtype: html.Div
    """

    @app.callback(
        Output(ids.NUMBER_STATIONS, "children"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    def update_number_stations(
            _, buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        # print("updating numbers")
        task_id = dash_app.slug

        # Get the data

        lines = get_number_of_stations(task_id)

        styles = [
            {"fontFamily": "Helvetica", "fontSize": "25px", "position": "relative", "top": "0", "left": "0"},
            {"fontFamily": "Helvetica", "fontSize": "48px", "textAlign": "center"},
        ]

        html_div = []

        for line, style in zip(lines, styles):
            html_div.append(html.H2(line, style=style))
        number_divs = html.Div(html_div)
        return html.Div(number_divs, id=ids.NUMBER_STATIONS)

    return html.Div(id=ids.NUMBER_STATIONS)


def render_bus_utilization(app: Dash) -> html.Div:
    """ """

    @app.callback(
        Output(ids.BUS_UTILIZATION, "children"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    def update_bus_utilization(
            _, buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        # print("updating numbers")
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        result_dict = data.get_scenario_duration(task_id)

        df = data.get_duration_as_dataframe(s.id, buses)
        all_rotations_duration = df["duration"].sum()

        # Get the data
        lines = [
            "Durchschnittliche Busauslastung:",
            str(round((result_dict["duration"].total_seconds() / all_rotations_duration) * 100, 3))
            + " %",
        ]

        styles = [
            {"fontFamily": "Helvetica", "fontSize": "25px", "position": "relative", "top": "0", "left": "0"},
            {"fontFamily": "Helvetica", "fontSize": "48px", "textAlign": "center"},
        ]

        html_div = []

        for line, style in zip(lines, styles):
            html_div.append(html.H2(line, style=style))
        number_divs = html.Div(html_div)
        return html.Div(number_divs, id=ids.BUS_UTILIZATION)

    return html.Div(id=ids.BUS_UTILIZATION)


def render_station_most_served(app: Dash) -> html.Div:
    """ """

    @app.callback(
        Output(ids.STATION_MOST_SERVED, "children"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    def update_station_most_served(
            _, buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        # print("updating numbers")
        task_id = dash_app.slug

        lines = data.get_frequently_served_station(task_id)
        styles = [
            {"fontFamily": "Helvetica", "fontSize": "25px", "position": "relative", "top": "0", "left": "0"},
            {"fontFamily": "Helvetica", "fontSize": "48px", "textAlign": "center"},
        ]

        html_div = []

        for line, style in zip(lines, styles):
            html_div.append(html.H2(line, style=style))
        number_divs = html.Div(html_div)
        return html.Div(number_divs, id=ids.STATION_MOST_SERVED)

    return html.Div(id=ids.STATION_MOST_SERVED)
