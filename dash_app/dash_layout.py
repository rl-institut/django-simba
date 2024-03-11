from dash import Dash, html
from . import (bus_dropdown,
               report_numbers,
               scatter_chart,
               bar_chart,
               histograms,
               activities_chart,
               piechart)


def create_layout(app: Dash) -> html.Div:
    # App layout
    return html.Div(
        [
            # html.H4(app.title, style={"textAlign": "center"}),
            html.Div(
                # THIS IS JUST A SPACER
                style={
                    "display": "inline-block",
                    "width": "100%",
                    "verticalAlign": "top",
                    "height": "20px",
                },
            ),
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
                children=block_first_third(app),
                style={
                    "display": "inline-block",
                    "width": "33%",
                    "verticalAlign": "top",
                    "height": "100px",
                },
            ),
            html.Div(
                children=block_second_third(app),
                style={
                    "display": "inline-block",
                    "width": "33%",
                    "verticalAlign": "top",
                    "height": "100px",
                },
            ),
            html.Div(
                children=block_third_third(app),
                style={
                    "display": "inline-block",
                    "width": "33%",
                    "verticalAlign": "top",
                    "height": "100px",
                },
            ),
            html.Div(
                children=block_top_left(app),
                style={
                    "display": "inline-block",
                    "width": "50%",
                    "verticalAlign": "top",
                    "height": "200px",
                },
            ),
            html.Div(
                children=block_top_right(app),
                style={
                    "display": "inline-block",
                    "width": "49%",
                    "verticalAlign": "top",
                    "height": "400px",
                },
            ),
            html.Div(
                children=block_lower_left(app),
                style={
                    "display": "inline-block",
                    "width": "50%",
                    "verticalAlign": "top",
                    "height": "400px",
                },
            ),
            html.Div(
                children=block_bottom_center(app),
                style={"display": "inline-block", "width": "100%", "height": "600px"},
            ),
        ]
    )


def block_first_third(app) -> list[html.Div]:
    return [report_numbers.render_longest_rotation(app)]


def block_second_third(app) -> list[html.Div]:
    return [report_numbers.render_shortest_rotation(app)]


def block_third_third(app) -> list[html.Div]:
    return [report_numbers.render_number_of_buses(app)]


def block_top_center(app) -> list[html.Div]:
    return [bus_dropdown.render(app)]


def block_bottom_center(app):
    return [scatter_chart.render(app),
            scatter_chart.render_power_draw(app),
            # histograms.render_soc(app),
            histograms.render_minimal_soc(app),
            activities_chart.render(app),
            histograms.render_rotation_duration(app),
            histograms.render_rotation_distance(app),
            histograms.render_dist_dur(app)]


def block_top_left(app) -> list[html.Div]:
    return [piechart.render_bustype(app)]


def block_lower_left(app) -> list[html.Div]:
    return [piechart.render_critical_rotations(app)]


def block_top_right(app):
    return [activities_chart.render_performance(app)]
