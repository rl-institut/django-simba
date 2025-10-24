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

# NOTE: This could be turned on if eflips schedule reader can gurantee concurrent writes

# app.conf.task_serializer = "pickle"
# app.conf.result_serializer = "pickle"
# app.conf.accept_content = ["pickle", "json"]

# Not all tasks can guarantee that they can run concurrently
# Tasks which should only run consecutively are put in the db_lock queue,
# where concurrency is set to 1
# the threads are then blocked to await the results, since queuing only works for async calls
# CELERY_TASK_QUEUES = (
#     Queue("default"),
#     Queue("db_lock"),
# )
# user shared_task(queue='db_lock') to register consecutive shared task
# needs extra worker witch concurrency =1


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
