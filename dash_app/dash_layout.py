import dash.exceptions
from dash import Dash, State, html, dcc, Input, Output
from dash.html import Div

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
    style,
)
from .eflips_plots import (
    get_ganttchart_scenario_eflips,
    get_vehicle_soc_plot_eflips,
    get_power_and_occupancy_plot_eflips,
    get_vehicle_by_click_eflips,
)

toc_dict = {
    "title": ["Tablelle minimaler SOC Werte", "Zeitfolgen", "Aktivitätsdiagramme", "Histogramme"],
    "id": ["section-1", "section-2", "section-3", "section-4"],
}


def create_layout(app: Dash) -> Div:
    # App layout
    @app.callback(
        [Output("tab-simulation", "disabled"), Output("tab-kpi", "disabled")],
        [Input("tab-simulation", "value")],
    )
    def update_tab(_, session_state=None, dash_app=None, **kwargs):
        disable_tabs = data.get_sim_done_status(dash_app.slug)
        return disable_tabs, disable_tabs

    @app.callback(
        [Output(ids.MEMOIZER_DONE, "data"), Output(ids.BUS_DROPDOWN, "data")],
        [Input(ids.BUS_DROPDOWN_RAW, "value")],
    )
    def memoize_all_data(buses: list[str], session_state=None, dash_app=None, **kwargs):
        scenario = Scenario.objects.get(task_id=dash_app.slug)
        _ = data.recent_memoizer(data.get_all_event_info, scenario.id)(scenario.id)
        _ = data.recent_memoizer(data.get_vehicle_dictionaries, scenario.id)(scenario.id)
        _ = data.recent_memoizer(data.get_all_trip_info, scenario.id)(scenario.id)
        _ = data.recent_memoizer(data.get_all_routes, scenario.id)(scenario.id)
        _ = data.recent_memoizer(data.get_all_powerdraw_as_dataframe, scenario.id)(scenario.id)
        _ = data.recent_memoizer(data.get_rotation_dictionaries, scenario.id)(scenario.id)
        return (
            True,
            buses,
        )

    @app.callback(
        Output(ids.APPLY_DROPDOWN, "n_clicks"),
        Input(ids.BUS_DROPDOWN, "data"),
        State(ids.APPLY_DROPDOWN, "n_clicks"),
    )
    def trigger_apply_once(_, n_clicks, session_state=None, dash_app=None, **kwargs):
        if n_clicks is None:
            return 0
        raise dash.exceptions.PreventUpdate

    # Dynamically generate the callback based on the number of sections
    inputs = [Input(f"button-to-{section_id}", "n_clicks") for section_id in toc_dict["id"]]
    inputs.append(Input("button-to-toc", "n_clicks"))

    @app.callback(Output("trigger_toc", "children"), inputs, prevent_initial_call=True)
    def update_output(*args):
        ctx = dash.callback_context
        if not ctx.triggered:
            button_id = "button-to-section-1"
        else:
            button_id = ctx.triggered[0]["prop_id"].split(".")[0]
        return button_id

    # Generate the clientside callback dynamically using string comprehension
    scroll_js = """
    function(button_id) {
        console.log(button_id);
        const section_ids = [%s];
        section_ids.forEach(function(section_id) {
            if (button_id === 'button-to-' + section_id) {
                document.getElementById(section_id).scrollIntoView({ behavior: 'smooth' });
            }
        });
        if (button_id === 'button-to-toc') {
            document.getElementById('toc').scrollIntoView({ behavior: 'smooth' });
        }
        return '';
    }
    """ % ", ".join(
        [f"'{section_id}'" for section_id in toc_dict["id"]]
    )

    app.clientside_callback(
        scroll_js,
        Output("garbage-output-0", "children"),
        [Input("trigger_toc", "children")],
        prevent_initial_call=True,
    )

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
                    html.Div(
                        children=block_top_center(app),
                        style={
                            "display": "inline-block",
                            "width": "100%",
                            "verticalAlign": "top",
                            "display": "none",
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
                                        html.Br(),
                                        html.Hr(),
                                        html.H1("Inhaltsübersicht", id="toc"),
                                        html.Ul(
                                            [
                                                html.Li(
                                                    html.Button(
                                                        title,
                                                        id=f"button-to-{section_id}",
                                                        style=style.link_style,
                                                    )
                                                )
                                                for title, section_id in zip(
                                                    toc_dict["title"], toc_dict["id"]
                                                )
                                            ]
                                        ),
                                        html.Div(id="garbage-output-0", style={"display": "none"}),
                                        html.Div(id="trigger_toc", style={"display": "none"}),
                                        html.Hr(),
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
                                        html.H1(
                                            children="Simulation results of eflips-depot",
                                            style={"font": "arial"},
                                        ),
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
                                        html.Div(
                                            "Click on a group in legend to hide/show the group."
                                        ),
                                        dcc.Graph(id="gantt-chart"),
                                        html.Div(
                                            children=[
                                                html.H2(children="SoC-log of vehicle:"),
                                                html.Div(
                                                    id="click-data", style={"font-size": "20"}
                                                ),
                                                dcc.Graph(id="vehicle-soc-plot"),
                                            ]
                                        ),
                                        html.Div(
                                            children=[
                                                html.H2(
                                                    children="Power and occupancy of current depot"
                                                ),
                                                dcc.Graph(id="power-and-occupancy-plot"),
                                            ]
                                        ),
                                    ],
                                ),
                            ]
                        ),
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
        style.make_next_heading(0, toc_dict),
        report_numbers.critical_rotations(app),
        style.make_next_heading(1, toc_dict),
        # html.P('Content of Section 1...'),
        scatter_chart.render(app),
        # scatter_chart.render_power_draw(app),
        # scatter_chart.render_scenario_powerdraw(app),
        # scatter_chart.render_single_station_occupation(app),
        # scatter_chart.render_station_occupation(app),
        style.make_next_heading(2, toc_dict),
        # html.P('Content of Section 2...'),
        activities_chart.render(app),
        style.make_next_heading(3, toc_dict),
        # html.P('Content of Section 3...'),
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
