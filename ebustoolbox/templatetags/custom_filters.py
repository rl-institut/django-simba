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
        else:
            if field.form.initial.get(field.name):
                out += f"value={field.form.initial.get(field.name)}"
    except AttributeError:
        pass

    return out


@register.filter
def get_item(obj, key):
    """Allow to get items from dict but also non-dict objects, without .get Method"""
    try:
        return obj[key]
    except KeyError:
        return None


@register.filter
def get_attr(obj, key):
    """Safely gets an attribute from an object."""
    return getattr(obj, key, None)
