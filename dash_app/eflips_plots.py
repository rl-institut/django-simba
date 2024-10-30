import eflips.eval.output.prepare as output_prepare
import eflips.eval.output.visualize as output_visualize

from eflips.model import Depot

from typing import Dict, Any

import sqlalchemy
from dash import Input, Output, State, Dash
from dash.exceptions import PreventUpdate
from eflips.model import Area
from sqlalchemy.orm import Session

import plotly.graph_objects as go

from . import (
    ids,
    data,
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
        Output(ids.EFLIPS_GANTT, "figure"),
        Output(ids.EFLIPS_SCENARIO_NAME, "children"),
        Output(ids.EFLIPS_NUM_VEHICLES, "children"),
        Output(ids.EFLIPS_TASK_ID, "data"),
        Input(ids.EFLIPS_COLORSCHEME_DROPDOWN, "value"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
        State(ids.BUS_DROPDOWN, "data"),
    )
    def get_ganttchart_scenario(
        color_scheme_dropdown: str, _, busses, session_state: Dict[str, Any] | None
    ):
        """
        Takes a value from the dropdown as a scenario ID and returns a plotly.express.timeline object
        representing the Gantt chart of the scenario to be used in an HTML layout.

        :param color_scheme_dropdown: A string from color-scheme-dropdown representing whether
        the Gantt chart should be colored by event type or by SOC.
        :param session_state: A dictionary containing the task ID.
        :return: A tuple of a plotly.express.timeline object, a string of the scenario name,
                 and a string of the number of vehicles in the scenario.
        """

        # Check if the session state and task ID are correctly set
        if session_state is None or "task_id" not in session_state:
            raise ValueError(
                "The session state must be set, and the task ID must be in the session state."
            )

        from ebustoolbox.models import Scenario as ebusScenario

        task_id = session_state["task_id"]

        # Check the simulation status
        if not data.get_sim_done_status(task_id):
            return go.Figure(layout=dict(template="plotly")), "", "", task_id
        else:

            try:
                engine = _create_engine_from_postgis_url()
                with Session(engine) as session:
                    scenario = ebusScenario.objects.get(task_id=task_id)
                    scenario_id = scenario.id
                    scenario_name = scenario.name

                    depot_events = output_prepare.depot_event(scenario_id, session)
                    depot_events = depot_events[depot_events["vehicle_id"].astype(int).isin(busses)]
                    num_vehicles = depot_events["vehicle_id"].nunique()

                    color_scheme = {
                        "Event Type": "event_type",
                        "State of Charge": "soc",
                        "Location": "location",
                    }

                    fig = output_visualize.depot_event(
                        depot_events, color_scheme[color_scheme_dropdown]
                    )

            finally:
                engine.dispose()

            return (
                fig,
                scenario_name,
                f"Total number of vehicles: {num_vehicles}",
                task_id,
            )


def get_vehicle_by_click_eflips(app: Dash):
    @app.callback(
        Output(ids.EFLIPS_CLICK_DATA, "children"),
        Input(ids.EFLIPS_GANTT, "clickData"),
    )
    def get_vehicle_by_click(clickData):
        if clickData is None:
            raise PreventUpdate
        vehicle_id = clickData["points"][0]["y"]
        return vehicle_id


def get_vehicle_soc_plot_eflips(app: Dash):
    @app.callback(
        Output(ids.EFLIPS_VEHICLE_SOC, "figure"),
        Input(ids.EFLIPS_CLICK_DATA, "children"),
        Input(ids.EFLIPS_TASK_ID, "data"),
    )
    def get_vehicle_soc_plot(vehicle_id: int, task_id: str):

        fig = go.Figure(layout=dict(template="plotly"))

        if not data.get_sim_done_status(task_id):
            if vehicle_id is None:
                raise PreventUpdate

            try:
                engine = _create_engine_from_postgis_url()
                with Session(engine) as session:
                    vehicle_soc, descriptions = output_prepare.vehicle_soc(vehicle_id, session)
                    fig = output_visualize.vehicle_soc(vehicle_soc, descriptions)

            except Exception as e:
                print(f"An error occurred: {e}")
                raise

            finally:
                engine.dispose()

        return fig


def get_power_and_occupancy_plot_eflips(app: Dash):
    @app.callback(
        Output(ids.EFLIPS_POWER_AND_OCCUPANCY, "figure"),
        Input(ids.EFLIPS_TASK_ID, "data"),
    )
    def get_power_and_occupancy_plot(task_id: str):

        fig = go.Figure(layout=dict(template="plotly"))

        if not data.get_sim_done_status(task_id):

            from ebustoolbox.models import Scenario as ebusScenario

            try:
                engine = _create_engine_from_postgis_url()
                with Session(engine) as session:
                    scenario = ebusScenario.objects.get(task_id=task_id)
                    scenario_id = scenario.id
                    all_areas = session.query(Area).filter(Area.scenario_id == scenario_id).all()
                    all_area_ids = [area.id for area in all_areas]

                    prepared_data = output_prepare.power_and_occupancy(all_area_ids, session)
                    fig = output_visualize.power_and_occupancy(prepared_data)

            except Exception as e:
                print(f"An error occurred: {e}")
                raise

            finally:
                engine.dispose()

        return fig

def get_specific_energy_eflips(app: Dash):
    @app.callback(
        Output(ids.EFLIPS_SPECIFIC_ENERGY, "figure"),
        Input(ids.EFLIPS_CLICK_DATA, "children"),
        Input(ids.EFLIPS_COLORSCHEME_DROPDOWN, "value"),
        Input(ids.APPLY_DROPDOWN, "n_clicks"),
    )
    def get_specific_energy_plot(
        color_scheme_dropdown: str, _, busses, session_state: Dict[str, Any] | None
    ):

        fig = go.Figure(layout=dict(template="plotly"))

        if not data.get_sim_done_status(session_state["task_id"]):

            from ebustoolbox.models import Scenario as ebusScenario

            if session_state is None:
                raise ValueError("The session state must be set")
            if "task_id" not in session_state:
                raise ValueError("The task id must be in the session state")

            try:
                engine = _create_engine_from_postgis_url()
                with Session(engine) as session:
                    scenario = ebusScenario.objects.get(task_id=session_state["task_id"])
                    scenario_id = scenario.id
                    prepared_data = output_prepare.specific_energy_consumption(scenario_id, session)
                    fig = output_visualize.specific_energy_consumption(prepared_data)

            except Exception as e:
                print(f"An error occurred: {e}")
                raise

            finally:
                engine.dispose()

        return fig
