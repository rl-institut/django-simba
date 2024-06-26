from dash import dash
import plotly.graph_objs as go
from dash import dcc, html


def set_styling(func):
    def wrapper(*args, **kwargs):
        font_family = "Helvetica"
        title_font_size = 25
        axis_font_size = 15
        hover_font_size = 20

        result = func(*args, **kwargs)

        def apply_styling(fig):
            fig.update_layout(
                font_family=font_family,
                font=dict(size=axis_font_size),
                title_font=dict(family=font_family, size=title_font_size),
                xaxis_title_font=dict(family=font_family, size=axis_font_size),
                yaxis_title_font=dict(family=font_family, size=axis_font_size),
                hoverlabel=dict(font=dict(family=font_family, size=hover_font_size))
            )
            return fig

        if isinstance(result, html.Div):
            child = result.children
            if isinstance(child, dcc.Graph) and isinstance(child.figure, go.Figure):
                child.figure = apply_styling(child.figure)
            else:
                print("Non stylable Object")
            return result

        if isinstance(result, go.Figure):
            return apply_styling(result)

        return result

    return wrapper
