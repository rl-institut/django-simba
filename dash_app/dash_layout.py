import dash.exceptions
from dash import Dash, html, dcc, Output, Input, State
from dash.html import Div
import dash_bootstrap_components as dbc
from ebustoolbox.models import Scenario
from . import (
    bus_dropdown,
    report_numbers,
    scatter_chart,
    histograms,
    activities_chart,
    piechart,
    data,
    ids,
)

from dash import html, dcc, Input, Output

from .eflips_plots import get_ganttchart_scenario_eflips, get_vehicle_soc_plot_eflips, \
    get_power_and_occupancy_plot_eflips, get_vehicle_by_click_eflips

global progress


def create_layout(app: Dash) -> Div:
    # App layout
    global progress
    progress = 0

    modal = dbc.Modal(
        [
            dbc.ModalHeader(
                dbc.ModalTitle("Your progress bar"),
                # close_button=False
                # ^^ important, otherwise the user can close the modal
                #    but the callback will be running still
            ),
            dbc.ModalBody(
                html.Div(
                    id="progress_container",
                    children=[
                        dcc.Interval(
                            id="load_interval",
                            n_intervals=0,
                            max_intervals=-1,  # <-- run inf
                            interval=1000,
                            disabled=False
                        ),
                        dbc.Progress(id="progress_bar", value=0, animated=True, style={"height": "30px"}),
                    ],
                ),
            ),
            dbc.ModalFooter(
                dbc.Button(
                    "Cancel",
                    id="cancel_button_id",
                    className="ms-auto",
                    n_clicks=0
                )
            )
        ],
        id="modal",
        is_open=False,
        backdrop="static",
        # keyboard=False
        # ^^ important, otherwise the user can close the modal via the ESC button
        #    but the callback will be running still
    )

    @app.callback(
        [Output("tab-simulation", "disabled"), Output("tab-kpi", "disabled")],
        [Input("tab-simulation", "value")],
    )
    def update_tab(_, session_state=None, dash_app=None, **kwargs):
        disable_tabs = data.get_sim_done_status(dash_app.slug)
        return disable_tabs, disable_tabs

    @app.callback(
        Output('progress_bar', 'value'),
        Output('modal', 'is_open'),
        Output("load_interval", "disabled"),
        Input('load_interval', 'n_intervals')
    )
    def update_progress_bar(n_intervals):
        global progress
        print(progress)
        if progress < 100:

            return progress, True, False
        else:
            progress = 0
            return 100, False, True

    @app.callback(
        [Output(ids.MEMOIZER_DONE, "data"), Output(ids.BUS_DROPDOWN, "data")],
        [Input(ids.BUS_DROPDOWN_RAW, "value")]
    )
    def memoize_all_data(buses: list[str], session_state=None, dash_app=None, **kwargs):
        global progress
        scenario = Scenario.objects.get(task_id=dash_app.slug)
        progress = 0
        _ = data.recent_memoizer(data.get_all_event_info, scenario.id)(scenario.id)
        progress = 20
        _ = data.recent_memoizer(data.get_vehicle_dictionaries, scenario.id)(scenario.id)
        progress = 40
        _ = data.recent_memoizer(data.get_all_trip_info, scenario.id)(scenario.id)
        progress = 60
        _ = data.recent_memoizer(data.get_all_routes, scenario.id)(scenario.id)
        progress = 70
        _ = data.recent_memoizer(data.get_all_powerdraw_as_dataframe, scenario.id)(scenario.id)
        progress = 80
        _ = data.recent_memoizer(data.get_rotation_dictionaries, scenario.id)(scenario.id)
        progress = 100
        return True, buses,

    @app.callback(
        Output(ids.APPLY_DROPDOWN, "n_clicks"),
        Input(ids.BUS_DROPDOWN, "data"),
        State(ids.APPLY_DROPDOWN, "n_clicks"),
    )
    def trigger_apply_once(_, n_clicks, session_state=None, dash_app=None, **kwargs):
        if n_clicks is None:
            return 0
        raise dash.exceptions.PreventUpdate

    return html.Div(
        [
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
                    modal,
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
                            dcc.Tab(
                                label="Depot Plots",
                                disabled=False,
                                children=[
                                    register_eflips_callbacks(app),
                                    html.H1(children="Simulation results of eflips-depot",
                                            style={"font": "arial"}),
                                    dcc.Store(id="task_id"),
                                    html.Div("Select a color-scheme:"),
                                    dcc.Dropdown(
                                        ["Event Type", "State of Charge", "Location"],
                                        "Event Type",
                                        id="color-scheme-dropdown",
                                        style={"width": "30%"},
                                    ),
                                    html.H2(id="scenario-name"),
                                    html.H2(id="num-vehicles"),
                                    html.Div("Click on a bar to reveal the vehicle log."),
                                    html.Div("Click on a group in legend to hide/show the group."),
                                    dcc.Graph(id="gantt-chart"),
                                    html.Div(
                                        children=[
                                            html.H2(children="SoC-log of vehicle:"),
                                            html.Div(id="click-data", style={"font-size": "20"}),
                                            dcc.Graph(id="vehicle-soc-plot"),
                                        ]
                                    ),
                                    html.Div(
                                        children=[
                                            html.H2(children="Power and occupancy of current depot"),
                                            dcc.Graph(id="power-and-occupancy-plot"),
                                        ]
                                    ),
                                ]
                            )
                        ]
                    ),
                ],
            ),
        ]
    )
def register_eflips_callbacks(app):
    get_ganttchart_scenario_eflips(app)
    get_vehicle_by_click_eflips(app)
    get_vehicle_soc_plot_eflips(app)
    get_power_and_occupancy_plot_eflips(app)

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
        scatter_chart.render_scenario_powerdraw(app),
        scatter_chart.render_single_station_occupation(app),
        scatter_chart.render_station_occupation(app),
        activities_chart.render(app),
        histograms.render_minimal_soc(app),
        histograms.render_minimal_soc_per_rotation(app),
        histograms.render_rotation_duration(app),
        histograms.render_rotation_distance(app),
        histograms.render_dist_dur(app),
    ]


def block_top_left(app) -> list[html.Div]:
    return [piechart.render_bustype(app)]


def block_top_left_KPI(app) -> list[html.Div]:
    return [
        report_numbers.render_total_distance(app),
        report_numbers.render_avg_consumption(app),
        report_numbers.render_number_stations(app),
        report_numbers.render_station_most_served(app),
        report_numbers.render_bus_utilization(app),
    ]


def block_top_right_KPI(app):
    return [piechart.render_critical_rotations(app)]
