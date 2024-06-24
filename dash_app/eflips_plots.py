from django.utils import html
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
from dash import Input, Output, State, Dash
from dash.exceptions import PreventUpdate
from eflips.model import Area
from sqlalchemy.orm import Session

import plotly.graph_objects as go

from . import (
    ids,
)


def _create_engine_from_postgis_url() -> sqlalchemy.engine.Engine:
    """
    Create a sqlalchemy engine from the DATABASE_URL environment variable.
    Replace the 'postgis' scheme with 'postgresql'
    """
    from ebustoolbox.tasks import create_db_url

    db_url = create_db_url()

    return sqlalchemy.create_engine(db_url)


def get_ganttchart_scenario_eflips(app: Dash):
    @app.callback(
        Output("gantt-chart", "figure"),
        Output("scenario-name", "children"),
        Output("num-vehicles", "children"),
        Output("task_id", "data"),
        Input("color-scheme-dropdown", "value"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    def get_ganttchart_scenario(color_scheme_dropdown: str, _, busses, session_state: Dict[str, Any] | None):
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


def get_vehicle_by_click_eflips(app: Dash):
    @app.callback(
        Output("click-data", "children"),
        Input("gantt-chart", "clickData"),
    )
    def get_vehicle_by_click(clickData):
        if clickData is None:
            raise PreventUpdate
        vehicle_id = clickData["points"][0]["y"]
        return vehicle_id


def get_vehicle_soc_plot_eflips(app: Dash):
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


def get_power_and_occupancy_plot_eflips(app: Dash):
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
