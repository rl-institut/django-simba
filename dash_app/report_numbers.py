from dash import Dash, html, dash_table
from . import ids
from dash.dependencies import Input, Output  # no fa401
from .data import (
    get_number_longest_rot,
    get_number_shortest_rot,
    get_number_of_buses,
    get_critical_rotations_and_score_as_dataframe,
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

    @app.callback(Output(ids.NUMBER_LONGEST_ROTATION, "children"), Input(ids.BUS_DROPDOWN, "value"))
    def update_report_numbers(
        buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        filter_dict = dict(task_id=task_id, vehicle__id__in=buses)

        # Get the data
        lines = get_number_longest_rot(filter_dict)

        styles = [
            {"fontSize": "25px", "position": "relative", "top": "0", "left": "0"},
            {"fontSize": "48px", "textAlign": "center"},
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
        Output(ids.NUMBER_SHORTEST_ROTATION, "children"), Input(ids.BUS_DROPDOWN, "value")
    )
    def update_report_numbers(
        buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        filter_dict = dict(task_id=task_id, vehicle__id__in=buses)

        # Get the data
        lines = get_number_shortest_rot(filter_dict)

        styles = [
            {"fontSize": "25px", "position": "relative", "top": "0", "left": "0"},
            {"fontSize": "48px", "textAlign": "center"},
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

    @app.callback(Output(ids.NUMBER_OF_BUSES, "children"), Input(ids.BUS_DROPDOWN, "value"))
    def update_report_numbers(
        buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        # print("updating numbers")
        task_id = dash_app.slug
        filter_dict = dict(task_id=task_id, vehicle__id__in=buses)

        # Get the data
        lines = get_number_of_buses(filter_dict)

        styles = [
            {"fontSize": "25px", "position": "relative", "top": "0", "left": "0"},
            {"fontSize": "48px", "textAlign": "center"},
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

    @app.callback(Output(ids.LIST_CRIT_ROTATIONS, "children"), Input(ids.BUS_DROPDOWN, "value"))
    def update_report_numbers(
        buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        s = Scenario.objects.get(task_id=task_id)

        # Get the data
        lines = get_critical_rotations_and_score_as_dataframe(s.id, buses)
        df_sorted = lines.sort_values(by="soc_end")
        # Generate HTML table dynamically
        table = html.Div(
            [
                html.Div(style={"height": "50px"}),  # Spacing div after table
                dash_table.DataTable(
                    id="table",
                    columns=[{"name": i, "id": i} for i in df_sorted.columns],
                    data=df_sorted.to_dict("records"),
                ),
                html.Div(style={"height": "75px"}),  # Spacing div after table
            ]
        )

        return table

    return html.Div(id=ids.LIST_CRIT_ROTATIONS)
