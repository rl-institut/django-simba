from dash import Dash, html, dcc, Output, Input
from dash.dcc import Loading
from dash.html import Div

from ebustoolbox.models import Scenario
from . import (
    bus_dropdown,
    report_numbers,
    scatter_chart,
    histograms,
    activities_chart,
    piechart,
    data, ids,
)


def create_layout(app: Dash) -> Div:
    # App layout
    @app.callback(
        [Output("tab-simulation", "disabled"), Output("tab-kpi", "disabled")],
        [Input("tab-simulation", "value")]
    )
    def update_tab(_, session_state=None, dash_app=None, **kwargs):
        disable_tabs = data.get_sim_done_status(dash_app.slug)
        return disable_tabs, disable_tabs

    @app.callback(
        [Output(ids.MEMOIZER_DONE, 'data'), Output(ids.BUS_DROPDOWN, 'data')],
        [Input(ids.BUS_DROPDOWN_RAW, "value")]
    )
    def memoize_all_data(buses: list[str], session_state=None, dash_app=None, **kwargs):
        scenario_id = Scenario.objects.get(task_id=dash_app.slug).id
        _ = data.recent_memoizer(data.get_all_event_info, scenario_id)(scenario_id)
        _ = data.recent_memoizer(data.get_all_powerdraw_as_dataframe, scenario_id)(scenario_id)
        _ = data.recent_memoizer(data.get_all_trip_info, scenario_id)(scenario_id)
        return True, buses

    return html.Div([
        dcc.Store(id=ids.MEMOIZER_DONE, data=False),  # Store to keep track of memoizer status
        dcc.Store(id=ids.BUS_DROPDOWN, data=[]),
        html.Div(
            id="_dash_app_container",
            style={
                "display": "inline-block",
                "width": "100%",
                "verticalAlign": "top",
            },
            children=[
                html.Div(
                    children=block_top_center(app),
                    style={
                        "display": "inline-block",
                        "width": "100%",
                        "verticalAlign": "top",
                    },
                ),
                dcc.Loading(
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
                                        children=block_top_left_KPI(app),
                                        style={
                                            "display": "inline-block",
                                            "width": "49%",
                                            "verticalAlign": "top",
                                        },
                                    ),
                                    html.Div(
                                        children=block_top_right_KPI(app),
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
                )
            ]
        )
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
    return [
        report_numbers.critical_rotations(app),
        scatter_chart.render(app),
        scatter_chart.render_power_draw(app),
        scatter_chart.render_station_occupation(app),
        activities_chart.render(app),
        histograms.render_minimal_soc(app),
        histograms.render_rotation_duration(app),
        histograms.render_rotation_distance(app),
        histograms.render_dist_dur(app),
    ]


def block_top_left(app) -> list[html.Div]:
    return [piechart.render_bustype(app)]


def block_top_left_KPI(app) -> list[html.Div]:
    return [report_numbers.render_total_distance(app),
            report_numbers.render_avg_consumption(app),
            report_numbers.render_number_stations(app),
            report_numbers.render_bus_utilization(app),
            ]


def block_top_right_KPI(app):
    return [piechart.render_critical_rotations(app)]
