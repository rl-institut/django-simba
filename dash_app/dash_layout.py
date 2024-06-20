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

from eflips.eval.output.prepare import (
    depot_event as prepare_depot_event,
    vehicle_soc as prepare_vehicle_soc,
    power_and_occupancy as prepare_power_and_occupancy,
)
from eflips.eval.output.visualize import (
    depot_event as visualize_depot_event,
    vehicle_soc as visualize_vehicle_soc,
    power_and_occupancy as visualize_power_and_occupancy,
)
from typing import Dict, Any

import sqlalchemy
from dash import html, dcc, Input, Output
from dash.exceptions import PreventUpdate
from django_plotly_dash import DjangoDash
from eflips.model import Area
from sqlalchemy.orm import Session

import plotly.graph_objects as go

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

    def _create_engine_from_postgis_url() -> sqlalchemy.engine.Engine:
        """
        Create a sqlalchemy engine from the DATABASE_URL environment variable.
        Replace the 'postgis' scheme with 'postgresql'
        """
        from ebustoolbox.tasks import create_db_url

        db_url = create_db_url()

        return sqlalchemy.create_engine(db_url)

    @app.callback(
        Output("gantt-chart", "figure"),
        Output("scenario-name", "children"),
        Output("num-vehicles", "children"),
        Output("task_id", "data"),
        Input("color-scheme-dropdown", "value"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    def get_ganttchart_scenario(color_scheme_dropdown: str,_,busses, session_state: Dict[str, Any] | None):
        """This function takes a value from dropdown as scenario id and returns a :class:`plotly.express.timeline` object
        representing the gantt chart of the scenario to be used in a html layout.
        :param color_scheme_dropdown: A string coming from color-scheme-dropdown representing whether
        the gantt chart should be colored by event type or by SOC
        :param session_state: A dictionary containing the task id
        :return: A tuple of a :class:`plotly.express.timeline` object, a string of the scenario name
        and a string of the number of vehicles in the scenario
        """

        from ebustoolbox.models import Scenario as ebusScenario

        # Make sure that the session state is set and that the task id is in the session state
        if session_state is None:
            raise ValueError("The session state must be set")
        if "task_id" not in session_state:
            raise ValueError("The task id must be in the session state")

        # Create a connection to the database

        engine = _create_engine_from_postgis_url()

        with Session(engine) as session:
            scenario = ebusScenario.objects.get(task_id=session_state["task_id"])
            scenario_id = scenario.id
            scenario_name = scenario.name

            depot_events = prepare_depot_event(scenario_id, session)
            depot_events = depot_events[depot_events['vehicle_id'].astype(int).isin(busses)]
            num_vehicles = depot_events["vehicle_id"].nunique()
            color_scheme = {
                "Event Type": "event_type",
                "State of Charge": "soc",
                "Location": "location",
            }

            fig = visualize_depot_event(depot_events, color_scheme[color_scheme_dropdown])

            fig.update_layout(height=num_vehicles * 10 + 250)

        engine.dispose()
        return fig, scenario_name, f"Total number of vehicles:{num_vehicles}", session_state["task_id"]

    @app.callback(
        Output("click-data", "children"),
        Input("gantt-chart", "clickData"),
    )
    def get_vehicle_by_click(clickData):
        if clickData is None:
            raise PreventUpdate
        vehicle_id = clickData["points"][0]["y"]
        return vehicle_id

    @app.callback(
        Output("vehicle-soc-plot", "figure"),
        Input("click-data", "children"),
    )
    def get_vehicle_soc_plot(vehicle_id: int):
        if vehicle_id is None:
            raise PreventUpdate

        engine = _create_engine_from_postgis_url()

        with Session(engine) as session:
            vehicle_soc, descriptions = prepare_vehicle_soc(vehicle_id, session)
            fig = visualize_vehicle_soc(vehicle_soc, descriptions)

        engine.dispose()
        return fig

    @app.callback(
        Output("power-and-occupancy-plot", "figure"),
        Input("task_id", "data"),
    )
    def get_power_and_occupancy_plot(task_id: str):
        from ebustoolbox.models import Scenario as ebusScenario

        engine = _create_engine_from_postgis_url()

        with Session(engine) as session:
            scenario = ebusScenario.objects.get(task_id=task_id)
            scenario_id = scenario.id
            all_areas = session.query(Area).filter(Area.scenario_id == scenario_id).all()
            all_area_ids = [area.id for area in all_areas]

            try:
                prepared_data = prepare_power_and_occupancy(all_area_ids, session)
                fig = go.Figure(layout=dict(template="plotly"))
                fig = visualize_power_and_occupancy(prepared_data)
            except ValueError:
                fig = go.Figure(layout=dict(template="plotly"))
        engine.dispose()
        return fig

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
    return [
        report_numbers.render_total_distance(app),
        report_numbers.render_avg_consumption(app),
        report_numbers.render_number_stations(app),
        report_numbers.render_station_most_served(app),
        report_numbers.render_bus_utilization(app),
    ]


def block_top_right_KPI(app):
    return [piechart.render_critical_rotations(app)]
