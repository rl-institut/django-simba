from django import template

register = template.Library()


@register.filter
def widget_attrs(field):
    """Extracts widget attributes as key=value pairs inlcuding id and name and value

    This allows using the widget attributes in stylized front end inputs.
    """
    attrs = field.subwidgets[0].data["attrs"]
    out = ""
    for key, value in attrs.items():
        if isinstance(value, bool):
            if not value:
                continue
            else:
                out += f"{key} "
                continue
        out += f"{key}={value} "

    out += f"id={field.auto_id} "
    out += f"name={field.html_name} "
    out += f"type={field.field.widget.input_type} "
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
def subwidget_data(field):
    assert len(field.subwidgets) == 1
    attrs_data = {key: value for key, value in field.subwidgets[0].data.items()}
    out = ""
    if attrs_data["is_hidden"]:
        attrs_data["hidden"] = ""
    del attrs_data["is_hidden"]
    if attrs_data["required"]:
        attrs_data["is_required"] = ""
    del attrs_data["is_required"]
    if attrs_data["is_initial"]:
        raise NotImplementedError
    del attrs_data["is_initial"]
    for key, value in attrs_data:
        out += f"{key}={value} "
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
