import plotly.graph_objects as go


def set_styling(fig, font_family="Helvetica", title_font_size=25, axis_font_size=15, hover_font_size=20):
    """
    Updates the font properties in a Plotly figure.

    Parameters:
    fig (go.Figure): The Plotly figure object to update.
    font_family (str): The font family to apply to the figure.
    title_font_family (str): The font family to apply to the title.
    axis_font_size (int): The font size for the axis titles and labels.
    title_font_size (int): The font size for the plot title.
    hover_font_size (int): The font size for the hover labels.

    Returns:
    go.Figure: The updated Plotly figure object with the specified font settings.
    """
    fig.update_layout(
        font_family=font_family,
        font=dict(size=axis_font_size),
        title_font=dict(family=font_family, size=title_font_size),
        xaxis_title_font=dict(family=font_family, size=axis_font_size),
        yaxis_title_font=dict(family=font_family, size=axis_font_size),
        hoverlabel=dict(font=dict(family=font_family, size=hover_font_size))
    )
    return fig
