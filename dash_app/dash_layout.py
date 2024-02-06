from dash import Dash, html, dcc
from . import bus_dropdown, report_numbers, histograms
import plotly.graph_objs as go

# def create_layout(app: Dash) -> html.Div:
#     return html.Div(
#         className="app-div",
#         children=[
#             html.H1(app.title),
#             html.Hr(),
#             html.Div(className="dropdown-contain", children=[bus_dropdown.render(app)]),
#             bar_chart.render(app),
#         ],
#     )


def create_layout(app: Dash) -> html.Div:
    # App layout
    return html.Div(
        [
            html.H1(app.title, style={"textAlign": "center"}),
            html.Div(
                children=block_top_center(app),
                style={"display": "inline-block", "width": "100%", "verticalAlign": "top"},
            ),
            html.Div(
                children=block_top_left(app),
                style={"display": "inline-block", "width": "50%", "verticalAlign": "top"},
            ),
            html.Div(
                children=block_top_right(app), style={"display": "inline-block", "width": "50%"}
            ),
            html.Div(
                children=block_bottom_center(app),
                style={"display": "inline-block", "width": "100%"},
            ),
        ]
    )


def block_top_center(app) -> list[html.Div]:
    return [bus_dropdown.render(app)]


def block_top_left(app) -> list[html.Div]:
    return [report_numbers.render(app)]


def block_top_right(app):
    pie_data = {"labels": ["A", "B", "C"], "values": [30, 40, 30], "type": "pie"}
    return [
        dcc.Graph(
            id="pie-chart",
            figure={
                "data": [go.Pie(pie_data)],
                "layout": {"margin": {"t": 0, "r": 0, "l": 0, "b": 0}, "showlegend": False},
            },
        ),  # Pie chart
    ]


def block_bottom_center(app):
    bar_data = {"x": ["Category 1", "Category 2"], "y": [20, 30], "type": "bar"}

    return [
        dcc.Graph(
            id="bar-chart",
            figure={
                "data": [go.Bar(bar_data)],
                "layout": {"margin": {"t": 0, "r": 0, "l": 0, "b": 0}},
            },
        ),  # Bar chart

        histograms.render(app)
    ]
