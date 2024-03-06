from dash import Dash, html, dcc
import plotly.graph_objects as go
from . import ids
from dash.dependencies import Input, Output  # no fa401


def render(app: Dash) -> html.Div:
    @app.callback(Output(ids.BAR_CHART, "figure"), Input(ids.BUS_DROPDOWN, "value"))
    def update_scatter(buses: list[str], session_state=None, dash_app=None, **kwargs):
        task_id = dash_app.slug
        filter_dict = dict(task_id=task_id, vehicle__name_short__in=buses)

        # Get the data
        data = get_bar_plot_data(filter_dict)
        x_data = data[:, 1]
        y_data = data[:, 2]
        hover_data = data[:, 0]
        fig = go.Figure(
            [
                go.Bar(x=x_data, y=y_data, hovertext=hover_data),
            ]
        )

        fig.update_layout(
            margin=dict(l=20, r=20, t=20, b=20),
        )
        fig.update_layout(
            xaxis=dict(tickmode="linear", tick0=min(x_data), dtick=x_data[1] - x_data[0])
        )
        fig.update_layout(showlegend=False)
        return fig

    return html.Div(dcc.Graph(id=ids.BAR_CHART), style={"verticalAlign": "top"})
