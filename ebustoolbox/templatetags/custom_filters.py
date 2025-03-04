from django import template

register = template.Library()


@register.filter
def widget_attrs(field):
    """Extracts widget attributes as key=value pairs inlcuding id and name and value

    This allows using the widget attributes in stylized front end inputs.
    """
    attrs = field.field.widget.attrs
    out = ""
    for key, value in attrs.items():
        try:
            float(value)
            out += f"{key}={value} "
            continue
        except ValueError:
            out += f'{key}="{value}" '

    out += f"id={field.auto_id} "
    out += f"name={field.html_name} "
    try:
        if field.data:
            out += f"value={field.data} "
    except AttributeError:
        pass

    return out
