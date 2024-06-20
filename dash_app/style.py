from dash import dash
import plotly.graph_objs as go


def set_styling(func):
    def wrapper(*args, **kwargs):

        font_family = "Helvetica"
        title_font_size = 25
        axis_font_size = 15
        hover_font_size = 20
        print("HERER")

        fig = func(*args, **kwargs)
        print(type(fig))
        # Check if fig is of type plotly.graph_objs.Figure
        if not isinstance(fig, go.Figure):
            return fig

        fig.update_layout(
            font_family=font_family,
            font=dict(size=axis_font_size),
            title_font=dict(family=font_family, size=title_font_size),
            xaxis_title_font=dict(family=font_family, size=axis_font_size),
            yaxis_title_font=dict(family=font_family, size=axis_font_size),
            hoverlabel=dict(font=dict(family=font_family, size=hover_font_size))
        )
        return fig
    return wrapper