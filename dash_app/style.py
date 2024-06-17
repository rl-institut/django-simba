import plotly.graph_objects as go


def set_styling(fig, font_family="Times New Roman", title_font_size=40, axis_font_size=12, hover_font_size=None):
    """
    Updates all font properties in a Plotly figure to the specified font family,
    and optionally sets the title, axis, and hover font sizes.

    Parameters:
    fig (go.Figure): The Plotly figure object to update.
    font_family (str): The font family to apply to the figure.
    title_font_size (int, optional): The font size for the plot title.
    axis_font_size (int, optional): The font size for the axis titles and labels.
    hover_font_size (int, optional): The font size for the hover labels.

    Returns:
    go.Figure: The updated Plotly figure object with the specified font settings.
    """
    def update_font(dict_obj):
        if isinstance(dict_obj, dict):
            for key, value in dict_obj.items():
                print(key, value)
                if isinstance(value, dict):
                    update_font(value)
                elif key == 'font':
                    if 'family' in value:
                        value['family'] = font_family
                    else:
                        value.update({'family': font_family})
                elif key == 'title' and isinstance(value, dict):
                    value.setdefault('font', {}).update({'family': font_family})

    # Update the layout fonts
    if 'font' in fig.layout:
        fig.layout.font.family = font_family
    else:
        fig.layout.font = go.layout.Font(family=font_family)

    # Update titles and other nested font properties
    update_font(fig.layout)

    # Update title font size
    if title_font_size is not None:
        if 'title' in fig.layout and isinstance(fig.layout.title, dict):
            fig.layout.title.font.update({'size': title_font_size})

    # Update axis font size
    if axis_font_size is not None:
        if 'xaxis' in fig.layout and isinstance(fig.layout.xaxis, dict):
            fig.layout.xaxis.title.font.update({'size': axis_font_size})
            fig.layout.xaxis.tickfont.update({'size': axis_font_size})
        if 'yaxis' in fig.layout and isinstance(fig.layout.yaxis, dict):
            fig.layout.yaxis.title.font.update({'size': axis_font_size})
            fig.layout.yaxis.tickfont.update({'size': axis_font_size})

    # Update annotations
    if 'annotations' in fig.layout:
        for annotation in fig.layout.annotations:
            if 'font' in annotation:
                annotation.font.family = font_family

    # Update legend fonts
    if 'legend' in fig.layout:
        if 'font' in fig.layout.legend:
            fig.layout.legend.font.family = font_family
        else:
            fig.layout.legend.font = go.layout.legend.Font(family=font_family)

    # Update coloraxis colorbar fonts
    if 'coloraxis' in fig.layout:
        if 'colorbar' in fig.layout.coloraxis:
            fig.layout.coloraxis.colorbar.title.font.family = font_family

    # Update hover labels (tooltips)
    if 'hoverlabel' in fig.layout:
        if 'font' in fig.layout.hoverlabel:
            fig.layout.hoverlabel.font.family = font_family
            if hover_font_size is not None:
                fig.layout.hoverlabel.font.size = hover_font_size
        else:
            fig.layout.hoverlabel.font = go.layout.hoverlabel.Font(family=font_family, size=hover_font_size)

    return fig