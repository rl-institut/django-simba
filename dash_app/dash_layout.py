from dash import Dash, html
from . import bus_dropdown, report_numbers, scatter_chart, bar_chart, histograms


def create_layout(app: Dash) -> html.Div:
    # App layout
    return html.Div(
        [
            html.H1(app.title, style={"textAlign": "center"}),
            html.Div(
                children=block_top_center(app),
                style={
                    "display": "inline-block",
                    "width": "100%",
                    "verticalAlign": "top",
                    "height": "20%",
                },
            ),
            html.Div(
                children=block_top_left(app),
                style={"display": "inline-block", "width": "50%", "verticalAlign": "top"},
            ),
            html.Div(
                children=block_top_right(app),
                style={
                    "display": "inline-block",
                    "width": "50%",
                    "verticalAlign": "top",
                    "height": "300px",
                },
            ),
            html.Div(
                children=block_bottom_center(app),
                style={"display": "inline-block", "width": "100%", "height": "300px"},
            ),
        ]
    )


def block_top_center(app) -> list[html.Div]:
    return [bus_dropdown.render(app)]


def block_top_left(app) -> list[html.Div]:
    return [report_numbers.render(app)]


def block_top_right(app):
    return [scatter_chart.render(app)]


def block_bottom_center(app):
    return [histograms.render(app)]
