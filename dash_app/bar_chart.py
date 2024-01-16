from dash import Dash, html, dcc
import plotly.express as px
from . import ids
from dash.dependencies import Input, Output  # no fa401
from ebustoolbox.models import Scenario, Rotation
import pandas as pd

SOME_DATA = px.data.medals_long()


def render(app: Dash) -> html.Div:
    all_busses = []

    @app.callback(
        [Output(ids.BAR_CHART, "children"), Output(ids.HIDDEN_DIV_ALL_BUSES, "children")],
        [Input(ids.BUS_DROPDOWN, "value"), Input(ids.HIDDEN_DIV_FOR_SLUG, "children")],
    )
    def update_bar_chart(
        buses: list[str], task_id, session_state=None, **kwargs
    ) -> (html.Div, list[str]):
        s = Scenario.objects.get(task_id=task_id)

        rotations = Rotation.objects.filter(scenario=s)
        all_buses = [r.vehicle.name_short for r in rotations]
        session_state["all_buses"] = all_buses
        filter_data = pd.DataFrame(
            {"type": "depb" if not r.allow_opportunity_charging else "oppb"}
            for r in rotations
            if r.vehicle_type.name in buses
        )
        # filter_data = SOME_DATA.query("nation in @buses")
        if filter_data.shape[0] == 0:
            return html.Div("No Data Selected")
        # fig = px.bar(filter_data, x="nation", y="count", color="nation", text="nation")
        fig = px.bar(filter_data, x="type")
        return html.Div(dcc.Graph(figure=fig), id=ids.BAR_CHART), [all_busses]

    #
    return html.Div(id=ids.BAR_CHART)
