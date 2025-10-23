from __future__ import absolute_import, unicode_literals
from celery import Celery
import os

from celery.result import AsyncResult

# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ebusdjango.settings")

app = Celery("ebusdjango.my_celery", backend="rpc://")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()

# Pickle is needed to return complex objects from workers
# This makes it necessary that the celery and server container are in sync at all times
# Otherwise pickling might fail

app.conf.task_serializer = "pickle"
app.conf.result_serializer = "pickle"
app.conf.accept_content = ["pickle", "json"]


def get_scheduled():
    i = app.control.inspect()
    return i.scheduled()


def get_active():
    i = app.control.inspect()
    return i.active()


def get_reserved():
    i = app.control.inspect()
    return i.reserved()


def get_result(task_id):
    return AsyncResult(task_id, app=app)
