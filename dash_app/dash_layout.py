from dash import Dash, html, dcc, Output, Input
from . import (
    bus_dropdown,
    report_numbers,
    scatter_chart,
    histograms,
    activities_chart,
    piechart,
    data,
)


def create_layout(app: Dash) -> html.Div:
    # App layout
    @app.callback(
        [Output("tab-simulation", "disabled"), Output("tab-kpi", "disabled")],
        [Input("tab-simulation", "value")]
    )
    def update_tab(_, session_state=None, dash_app=None, **kwargs):
        disable_tabs = data.get_sim_done_status(dash_app.slug)
        return disable_tabs, disable_tabs

    return html.Div(dcc.Loading(html.Div(
        id="_dash_app_container",
        style={
            "display": "inline-block",
            "width": "100%",
            "verticalAlign": "top",
        }, children=
        [
            html.Div(
                children=block_top_center(app),
                style={
                    "display": "inline-block",
                    "width": "100%",
                    "verticalAlign": "top",
                },
            ),
            dcc.Tabs(
                children=[
                    dcc.Tab(
                        label="Pre-Simulation Plots",
                        children=[
                            html.Div(
                                children=block_first_third(app),
                                style={
                                    "display": "inline-block",
                                    "width": "33%",
                                    "verticalAlign": "top",
                                },
                            ),
                            html.Div(
                                children=block_second_third(app),
                                style={
                                    "display": "inline-block",
                                    "width": "33%",
                                    "verticalAlign": "top",
                                },
                            ),
                            html.Div(
                                children=block_third_third(app),
                                style={
                                    "display": "inline-block",
                                    "width": "33%",
                                    "verticalAlign": "top",
                                },
                            ),
                            html.Div(
                                children=block_top_left(app),
                                style={
                                    "display": "inline-block",
                                    "width": "50%",
                                    "verticalAlign": "top",
                                },
                            ),
                        ],
                    ),
                    dcc.Tab(
                        label="KPI Tab",
                        id="tab-kpi",
                        value="tab-kpi",
                        disabled=True,
                        children=[
                            html.Div(
                                children=block_top_right(app),
                                style={
                                    "display": "inline-block",
                                    "width": "49%",
                                    "verticalAlign": "top",
                                },
                            ),
                        ],
                    ),
                    dcc.Tab(
                        label="Simulation Plots",
                        id="tab-simulation",
                        value="tab-simulation",
                        disabled=True,
                        children=[
                            html.Div(
                                children=block_bottom_center(app),
                                style={
                                    "display": "inline-block",
                                    "width": "100%",
                                },
                            ),
                        ],
                    ),
                ]
            ),
        ]
    )
    )
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
    return [
        report_numbers.critical_rotations(app),
        scatter_chart.render(app),
        scatter_chart.render_power_draw(app),
        scatter_chart.render_station_occupation(app),
        histograms.render_minimal_soc(app),
        activities_chart.render(app),
        histograms.render_rotation_duration(app),
        histograms.render_rotation_distance(app),
        histograms.render_dist_dur(app),
    ]


def block_top_left(app) -> list[html.Div]:
    return [piechart.render_bustype(app)]


def block_lower_left(app) -> list[html.Div]:
    return []


def block_top_right(app):
    return [piechart.render_critical_rotations(app)]
