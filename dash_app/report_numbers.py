from dash import Dash, html
from . import ids
from dash.dependencies import Input, Output  # no fa401
from .data import get_report_numbers_text


def render(app: Dash) -> html.Div:
    @app.callback(Output(ids.NUMBER_REPORT, "children"), Input(ids.BUS_DROPDOWN, "value"))
    def update_report_numbers(
        buses: list[str], session_state=None, dash_app=None, **kwargs
    ) -> html.Div:
        print("updating numbers")
        task_id = dash_app.slug
        filter_dict = dict(task_id=task_id, vehicle__name_short__in=buses)

        # Get the data
        lines, styles = get_report_numbers_text(filter_dict)
        html_div = []
        base_style = {"textAlign": "center"}
        for line, style in zip(lines, styles):
            current_style = base_style.copy()
            current_style.update(style)
            html_div.append(html.H2(line, style=current_style))
        number_divs = html.Div(html_div)
        return html.Div(number_divs, id=ids.NUMBER_REPORT)

    return html.Div(id=ids.NUMBER_REPORT)
