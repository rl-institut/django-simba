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

import eflips.eval.input.prepare as input_prepare
import eflips.eval.output.prepare as output_prepare
import eflips.eval.input.visualize as input_visualize
import eflips.eval.output.visualize as output_visualize

from eflips.model import Area, Vehicle, Depot

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


def get_rotated_rectangle_corners(rect):

    import numpy as np
    """
    Calculate the corners of a rotated rectangle.
    """
    x, y = rect.get_xy()
    width = rect.get_width()
    height = rect.get_height()
    angle = np.deg2rad(-45)




    # Define the corners of the rectangle before rotation
    corners = np.array([
        [x, y],
        [x + width, y],
        [x + width, y + height],
        [x, y + height]
    ])

    # Calculate the center of the rectangle for rotation
    center = np.array([x + width / 2, y + height / 2])

    # Rotate each corner around the center
    rotation_matrix = np.array([
        [np.cos(angle), -np.sin(angle)],
        [np.sin(angle), np.cos(angle)]
    ])

    rotated_corners = np.dot(corners - center, rotation_matrix) + center

    return rotated_corners

def matplotlib_to_plotly(fig, ax):
    import plotly.graph_objects as go
    import matplotlib.patches as patches


    # Create a Plotly figure
    plotly_fig = go.Figure()

    # Loop over each patch in the Matplotlib axis
    for patch in ax.patches:
        if isinstance(patch, patches.Rectangle):
            corners = get_rotated_rectangle_corners(patch)
            edgecolor = patch.get_edgecolor()
            facecolor = patch.get_facecolor()

            # Convert colors to RGB format
            edgecolor_rgb = f'rgb({int(edgecolor[0]*255)}, {int(edgecolor[1]*255)}, {int(edgecolor[2]*255)})'
            facecolor_rgb = f'rgb({int(facecolor[0]*255)}, {int(facecolor[1]*255)}, {int(facecolor[2]*255)})'

            # Add the polygon (rotated rectangle) to the Plotly figure
            plotly_fig.add_shape(
                type="path",
                path=f'M {corners[0][0]},{corners[0][1]} L {corners[1][0]},{corners[1][1]} L {corners[2][0]},{corners[2][1]} L {corners[3][0]},{corners[3][1]} Z',
                line=dict(color=edgecolor_rgb),
                fillcolor=facecolor_rgb,
            )

    # Add text if available
    for text in ax.texts:
        plotly_fig.add_annotation(
            x=text.get_position()[0],
            y=text.get_position()[1],
            text=text.get_text(),
            showarrow=False,
            font=dict(color=text.get_color(), size=15),
            xref="x",
            yref="y",
            xanchor="center",
            yanchor="middle",
            textangle=0  # Rotate text in Plotly
        )
        print("\n")
        print(text.get_text(), text.get_position()[0], text.get_position()[1])

    # Set the range of axes for better visibility
    plotly_fig.update_xaxes(
        scaleanchor="y",  # Lock the aspect ratio
        scaleratio=1,  # Ensure equal scaling
        range=[ax.get_xlim()[0], ax.get_xlim()[1]]
    )
    plotly_fig.update_yaxes(
        scaleanchor="x",  # Lock the aspect ratio
        scaleratio=1,  # Ensure equal scaling
        range=[ax.get_ylim()[0], ax.get_ylim()[1]]
    )

    # Set the range of axes for better visibility
    plotly_fig.update_xaxes(range=[ax.get_xlim()[0], ax.get_xlim()[1]])
    plotly_fig.update_yaxes(range=[ax.get_ylim()[0], ax.get_ylim()[1]])

    plotly_fig.update_layout(
        width=800,
        height=800  # Set height equal to width for a square figure
    )

    return plotly_fig

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
    def get_ganttchart_scenario(
        color_scheme_dropdown: str, _, busses, session_state: Dict[str, Any] | None
    ):
        """This function takes a value from dropdown as scenario id
        and returns a :class:`plotly.express.timeline` object
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
            depot_events = depot_events[depot_events["vehicle_id"].astype(int).isin(busses)]
            num_vehicles = depot_events["vehicle_id"].nunique()
            color_scheme = {
                "Event Type": "event_type",
                "State of Charge": "soc",
                "Location": "location",
            }

            fig = visualize_depot_event(depot_events, color_scheme[color_scheme_dropdown])

            fig.update_layout(height=num_vehicles * 10 + 250)

            # Example of the specific energy consumption visualization
            prepared_data = output_prepare.specific_energy_consumption(scenario_id, session)
            fig2 = output_visualize.specific_energy_consumption(prepared_data)

            # Example of using the arrival and departure SoC visualization
            prepared_data = output_prepare.departure_arrival_soc(scenario_id, session)
            fig3 = output_visualize.departure_arrival_soc(prepared_data)

            # Example of using the rotation info visualization
            prepared_data = input_prepare.rotation_info(scenario_id, session)
            fig4 = input_visualize.rotation_info(prepared_data)

            # Example of using the depot event visualization
            prepared_data = output_prepare.depot_event(scenario_id, session)
            fig5 = output_visualize.depot_event(prepared_data, color_scheme="event_type")

            # Example of using the depot activity visualization
            depot_id = (
                session.query(Depot.id)
                .filter(Depot.scenario_id == scenario_id)
                .limit(1)
                .one()[0]
            )

            area_blocks = output_prepare.depot_layout(depot_id, session)
            ad, fig6 = output_visualize.depot_layout(area_blocks)

            fig6.savefig("lol.png")

            print(ad)

            ax = fig6.gca()

            # Convert to Plotly
            plotly_fig = matplotlib_to_plotly(fig6, ax)

        engine.dispose()
        return (
            plotly_fig,
            scenario_name,
            f"Total number of vehicles:{num_vehicles}",
            session_state["task_id"],
        )


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
