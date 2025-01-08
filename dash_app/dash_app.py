from django_plotly_dash import DjangoDash
from .dash_layout import create_layout
from dash_bootstrap_components.themes import BOOTSTRAP
from django.apps import apps

# create basic stateless app


app = DjangoDash(
    "SimpleExampleApp", add_bootstrap_links=True, external_stylesheets=[BOOTSTRAP]
)  # replaces dash.Dash
app.title = "Ebus2030 dashboard"
app.layout = create_layout(app)

# this app runs in the background I think

# Get the database models for DashApp and Stateless app
DashApp = apps.get_model("django_plotly_dash", "DashApp")
StatelessApp = apps.get_model("django_plotly_dash", "StatelessApp")


# todo, check user too
def create_app(task_id: str):
    """
    Creates a specific Dash app instance associated with a task ID.

    :param task_id: The ID of the task.
    :type task_id: str

    :return: None
    :rtype: None
    """
    # Save the Stateless app
    sa1, created = StatelessApp.objects.get_or_create(
        app_name="SimpleExampleApp", slug="simple-example"
    )

    if not created:
        sa1.save()

    app_instance, created = DashApp.objects.get_or_create(
        stateless_app=sa1,
        instance_name=sa1.app_name + "_" + task_id,
        slug=task_id,
    )  # replaces dash.Dash
    if not created:
        app_instance.save()
