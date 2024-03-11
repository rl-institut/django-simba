from dash import Dash, html
from . import ids
from dash.dependencies import Input, Output  # no fa401
from .data import get_number_longest_rot, get_number_shortest_rot, get_number_of_buses


def render_longest_rotation(app: Dash) -> html.Div:
    @app.callback(Output(ids.NUMBER_LONGEST_ROTATION, "children"), Input(ids.BUS_DROPDOWN, "value"))
    def update_report_numbers(
            buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        filter_dict = dict(task_id=task_id, vehicle__name_short__in=buses)

        # Get the data
        lines = get_number_longest_rot(filter_dict)

        styles = [{"fontSize": "25px", "position": "relative", "top": "0", "left": "0"},
                  {"fontSize": "48px", "textAlign": "center"}]

        html_div = []

        for line, style in zip(lines, styles):
            html_div.append(html.H2(line, style=style))
        number_divs = html.Div(html_div)
        return html.Div(number_divs, id=ids.NUMBER_LONGEST_ROTATION)

    return html.Div(id=ids.NUMBER_LONGEST_ROTATION)


def render_shortest_rotation(app: Dash) -> html.Div:
    @app.callback(Output(ids.NUMBER_SHORTEST_ROTATION, "children"), Input(ids.BUS_DROPDOWN, "value"))
    def update_report_numbers(
            buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        task_id = dash_app.slug
        filter_dict = dict(task_id=task_id, vehicle__name_short__in=buses)

        # Get the data
        lines = get_number_shortest_rot(filter_dict)

        styles = [{"fontSize": "25px", "position": "relative", "top": "0", "left": "0"},
                  {"fontSize": "48px", "textAlign": "center"}]

        html_div = []

        for line, style in zip(lines, styles):
            html_div.append(html.H2(line, style=style))
        number_divs = html.Div(html_div)
        return html.Div(number_divs, id=ids.NUMBER_SHORTEST_ROTATION)

    return html.Div(id=ids.NUMBER_SHORTEST_ROTATION)


def render_number_of_buses(app: Dash) -> html.Div:
    @app.callback(Output(ids.NUMBER_OF_BUSES, "children"), Input(ids.BUS_DROPDOWN, "value"))
    def update_report_numbers(
            buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        print("updating numbers")
        task_id = dash_app.slug
        filter_dict = dict(task_id=task_id, vehicle__name_short__in=buses)

        # Get the data
        lines = get_number_of_buses(filter_dict)

        styles = [{"fontSize": "25px", "position": "relative", "top": "0", "left": "0"},
                  {"fontSize": "48px", "textAlign": "center"}]

        html_div = []

        for line, style in zip(lines, styles):
            html_div.append(html.H2(line, style=style))
        number_divs = html.Div(html_div)
        return html.Div(number_divs, id=ids.NUMBER_OF_BUSES)

    return html.Div(id=ids.NUMBER_OF_BUSES)
