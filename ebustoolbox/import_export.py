"""
Module for importing and exporting WeBus Scenarios.
Uses an Iterator to visit all relevant Scenario objects to export the Scenario to json"""

import io
from pathlib import Path
from django.core.exceptions import FieldError
from django.core.serializers import serialize  # noqa
from django.contrib.gis.db import models
import django.apps
from django.db.models import QuerySet
from django.db.models.fields.related import ForeignKey, ManyToManyField, OneToOneField

from rest_framework import serializers
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from ebustoolbox.models import (
    Scenario,
)


def visit_all_scenario_queries(visitor, scenario: Scenario):
    import django.apps

    ebus_models = django.apps.apps.get_app_config("ebustoolbox").get_models()
    # All relevant models have a foreign key to this scenario
    for model in ebus_models:
        # Scenario has a foreign key to its parent which we want to ignore. instead we use
        # the passed scenario.itself
        if model == Scenario:
            # vist expects a query
            visitor.visit(Scenario.objects.filter(id=scenario.id))
            continue
        try:
            visitor.visit(model.objects.filter(scenario=scenario))
        except FieldError:
            print(f"{model} has no field 'scenario'")


class ScenarioJSONExporter:
    def __init__(self):
        # Stores all objects to be serialized with the modelname as key
        self.object_data: dict[str, list[object]] = dict()
        self.parsed_data: dict[str, list[object]] = dict()
        self.serializers: dict[str, list[object]] = dict()

    def visit(self, elements: QuerySet):
        model_class: models.Model = elements.model
        serializer_class = generate_serializer(model_class)
        serializer = serializer_class(elements, many=True)
        self.object_data[elements.model.__qualname__] = serializer.data

    def renderJSON(self):
        return JSONRenderer().render(self.object_data)

    def loads(self, json_bytes):
        stream = io.BytesIO(json_bytes)
        data = JSONParser().parse(stream)
        self.parsed_data = data
        self.object_data = dict()

    def load(self, path: Path):
        with open(path, "rb") as f:
            self.loads(f.read())

    def generate_instances(self):
        for model_name, data in self.parsed_data.items():
            print(model_name)
            model_class: models.Model = django.apps.apps.get_model("ebustoolbox", model_name)
            serializer_class = generate_serializer(model_class)
            serializer = serializer_class(data=data, many=True)
            if serializer.is_valid():
                self.object_data[model_name] = []
                for i, instance_data in enumerate(serializer.validated_data):
                    # Add id data which is missing by default
                    instance_data["id"] = serializer.initial_data[i]["id"]
                    self.object_data[model_name].append(model_class(**instance_data))
            else:
                print(
                    f"Could not validate instances of {model_name} because of errors regarding {serializer._errors}"
                )
                fields = model_class._meta.fields
                # Since foreign keys are passed by id, the dict for instance generation has to be
                # adjusted from "name" to "name_id"
                field_name_lookup = [
                    (
                        field.name if not isinstance(field, ForeignKey) else field.name + "_id",
                        field.name,
                    )
                    for field in fields
                ]
                self.object_data[model_name] = []
                for instance_data in serializer.initial_data:
                    dict_data = {field[0]: instance_data[field[1]] for field in field_name_lookup}
                    self.object_data[model_name].append(model_class(**dict_data))

            self.serializers[model_name] = serializer

    def create_creation_order(self) -> list[str]:
        assert self.object_data != dict(), "Cannot create a creation order with no data"

        import django.apps

        ebus_models = {
            model.__qualname__
            for model in django.apps.apps.get_app_config("ebustoolbox").get_models()
        }
        # This ordering is based on the foreignkeys of each type. When a Model references another
        # model this model should be created first
        priority = [
            "Scenario",
            "BatteryType",
            "VehicleType",
            "VehicleClass",
            "Vehicle",
            "Rotation",
            "Line",
            "Station",
            "Route",
            "Trip",
            "StopTime",
            "Plan",
            "Process",
            "Depot",
            "Area",
            "Event",
        ]
        creation_order = priority + [*ebus_models.difference(priority)]
        for model_name in creation_order.copy():
            if model_name not in self.object_data:
                creation_order.pop(creation_order.index(model_name))
        return creation_order

    def adjust_foreign_keys(self):
        instance_lookup = dict()
        model_names = self.create_creation_order()
        import django.apps

        for model_name in model_names:
            model_class = django.apps.apps.get_model("ebustoolbox", model_name)
            instances = self.object_data[model_name]
            from ebustoolbox.util import get_next_id

            # Instances has at least a single element since its create_creation_order does not contain
            # empty lists
            next_id = get_next_id(model_class)
            lookup = dict()
            foreign_fields = [
                field
                for field in model_class._meta.fields
                if isinstance(field, ForeignKey) or isinstance(field, OneToOneField)
            ]
            for instance in instances:
                lookup[instance.id] = next_id
                instance.id = next_id
                next_id += 1
                for field in foreign_fields:
                    if not getattr(instance, field.name):
                        continue

                    try:
                        new_key = instance_lookup[field.related_model][
                            getattr(instance, field.name).id
                        ]
                        setattr(instance, field.name + "_id", new_key)
                    except KeyError:
                        print(
                            f"{model_class} Import Error: Field {field.name} could not be adjusted"
                            " with an imported Instance. Trying to set it to None"
                        )
                        setattr(instance, field.name, None)
            instance_lookup[model_class] = lookup

        for model_name in model_names:
            instances = self.object_data[model_name]
            model_class = django.apps.apps.get_model("ebustoolbox", model_name)
            model_class.objects.bulk_create(instances)

        # Now the ManyToMany relations are missing. They can be generated by using the inital_data
        # and finding ManyToMany fields
        already_created = set()  # noqa
        for model_name in model_names:
            many_fields = [
                field
                for field in model_class._meta.get_fields()
                if isinstance(field, ManyToManyField)
            ]
            for field in many_fields:
                pass


def generate_serializer(model_class: models.Model):
    class Meta:
        model = model_class
        # __all__ includes all fields inclunding ManyToManyRelationships
        fields = "__all__"

    serializer_class = type(
        f"{model_class.__name__}Serializer",
        (serializers.ModelSerializer,),
        {
            "Meta": Meta,
        },
    )
    return serializer_class


#
# from ebustoolbox.tests import build_scenario
#
# django_scenario, _, _ = build_scenario()
# exporter = ScenarioJSONExporter()
# visit_all_scenario_queries(exporter, django_scenario)
# visitor_objects_count = 0
# for model, x in exporter.object_data.items():
#     current = len(x)
#     print(model, current)
#     visitor_objects_count += current
# print(visitor_objects_count)
# json_data = exporter.renderJSON()
# type(json_data)
# json_data
# exporter.loads(json_data)
# type(exporter.parsed_data["Consumption"])
# exporter.generate_instances()
# stream = io.BytesIO(json_data)
# data = JSONParser().parse(stream)
# data
# gen = iter(data)
# model = next(gen)
# model
#
# MODEL = django.apps.apps.get_model("ebustoolbox", model)
# MODEL
# serializer_class = generate_serializer(MODEL)
# data[model]
# serializer = serializer_class(data=data[model], many=True)
# serializer.is_valid()
# serializer.validated_data[0]
# assert serializer.initial_data[0]["id"]
# # Cast to dict. This ignores ManyToManyRelationships.
# # Original id is part of initial_data but no of validated date
# original_scenario = model_to_dict(django_scenario)
# del original_scenario["id"]
# imported_scenario = model_to_dict(serializer.validated_data[0])
# model_to_dict(serializer.validated_data[0])
# del imported_scenario["id"]
# assert original_scenario == imported_scenario
# VehicleType._meta.get_fields()
#
# model = "VehicleType"
# MODEL = django.apps.apps.get_model("ebustoolbox", model)
# MODEL
# serializer_class = generate_serializer(MODEL)
# data[model]
# serializer = serializer_class(data=data[model], many=True)
# serializer.is_valid()
# serializer.validated_data[0]
# assert serializer.initial_data[0]["id"]
