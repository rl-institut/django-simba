"""
Module for importing and exporting WeBus Scenarios.
Uses an Iterator to visit all relevant Scenario objects to export the Scenario to json"""

import io
import logging
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
from ebustoolbox.util import get_next_id

logger = logging.getLogger("custom")


def visit_all_scenario_queries(visitor, scenario: Scenario):

    ebus_models = django.apps.apps.get_app_config("ebustoolbox").get_models()
    # All relevant models have a foreign key to this scenario
    for model in ebus_models:
        # Scenario has a foreign key to its parent which we want to ignore.
        # Instead we use the passed scenario itself
        if model == Scenario:
            # visit expects a query
            visitor.visit(Scenario.objects.filter(id=scenario.id))
            continue
        try:
            visitor.visit(model.objects.filter(scenario=scenario))
        except FieldError:
            logger.debug(f"{model} has no field 'scenario'")


class ScenarioJSONImporterExporter:
    def __init__(self):
        # Stores all objects to be serialized with the modelname as key
        self.object_data: dict[str, list[object]] = dict()
        self.parsed_data: dict[str, list[object]] = dict()
        self.instance_lookup: dict[str, dict[int, int]] = dict()

    def visit(self, elements: QuerySet):
        model_class: models.Model = elements.model
        serializer_class = generate_serializer(model_class)
        serializer = serializer_class(elements, many=True)
        if self.object_data.get(elements.model.__qualname__) is None:
            self.object_data[elements.model.__qualname__] = serializer.data
        else:
            # When multiple scenarios exported, we do not want the previous data to be overwritten
            self.object_data[elements.model.__qualname__].extend(serializer.data)

    def renderJSON(self):
        return JSONRenderer().render(self.object_data)

    def loads(self, json_bytes=None, in_memory_file=None):
        assert bool(json_bytes) != bool(in_memory_file), "Pass json_bytes xor in_memory file"
        if json_bytes:
            stream = io.BytesIO(json_bytes)
        else:
            stream = in_memory_file
        data = JSONParser().parse(stream)
        self.parsed_data = data
        self.object_data = dict()

    def load(self, path: Path):
        with open(path, "rb") as f:
            self.loads(f.read())

    def generate_instances(self):
        for model_name, data in self.parsed_data.items():
            model_class: models.Model = django.apps.apps.get_model("ebustoolbox", model_name)
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
            for instance_data in data:
                dict_data = {field[0]: instance_data[field[1]] for field in field_name_lookup}
                self.object_data[model_name].append(model_class(**dict_data))

    def create_creation_order(self) -> list[str]:
        assert self.object_data != dict(), "Cannot create a creation order with no data"
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
            "DepotConfigurationWish",
            "AreaInformation",
        ]
        creation_order = priority + [*ebus_models.difference(priority)]
        for model_name in creation_order.copy():
            if model_name not in self.object_data:
                creation_order.pop(creation_order.index(model_name))
        return creation_order

    def adjust_foreign_keys(self):
        self.instance_lookup = dict()
        model_names = self.create_creation_order()

        for model_name in model_names:
            model_class = django.apps.apps.get_model("ebustoolbox", model_name)
            instances = self.object_data[model_name]
            data = self.parsed_data[model_name]
            # Instances has at least a single element since its create_creation_order does not contain
            # empty lists
            next_id = get_next_id(model_class)
            self.instance_lookup[model_class] = {}
            foreign_fields = [
                field
                for field in model_class._meta.fields
                if isinstance(field, ForeignKey) or isinstance(field, OneToOneField)
            ]
            for instance, instance_data in zip(instances, data):
                self.instance_lookup[model_class][instance.id] = next_id
                instance.id = next_id
                next_id += 1
                for field in foreign_fields:
                    old_id = instance_data.get(field.name)
                    if old_id is None:
                        continue
                    try:
                        new_key = self.instance_lookup[field.related_model][old_id]
                        setattr(instance, field.name + "_id", new_key)
                    except KeyError:
                        logger.info(
                            f"{model_class} Import Error: Field {field.name} could not be adjusted"
                            " with an imported Instance. Trying to set it to None"
                        )
                        setattr(instance, field.name, None)

    def bulk_create(self):
        # Bulk create instances
        model_names = self.create_creation_order()
        for model_name in model_names:
            instances = self.object_data[model_name]
            model_class = django.apps.apps.get_model("ebustoolbox", model_name)
            model_class.objects.bulk_create(instances)

    def create_many_to_many(self):
        # Now the ManyToMany relations are missing. They can be generated by using the inital_data
        # and finding ManyToMany fields
        model_names = self.create_creation_order()
        already_created = set(
            django.apps.apps.get_model("ebustoolbox", model_name) for model_name in model_names
        )
        for model_name in model_names:
            model_class = django.apps.apps.get_model("ebustoolbox", model_name)
            many_fields = [
                field
                for field in model_class._meta.get_fields()
                if isinstance(field, ManyToManyField)
            ]
            for field in many_fields:
                through_model = getattr(model_class, field.name).through
                if through_model in already_created:
                    logger.debug(f"{through_model} already created")
                    continue
                already_created.add(through_model)
                fields = through_model._meta.fields

                # The Tables with many to many relationships can only consist of an id
                # and ForeignKeys. Other data is not passed to the serializer, right?
                assert "id" in (f.name for f in fields)
                assert len([f for f in fields if isinstance(f, ForeignKey)]) == len(fields) - 1 == 2
                # Make sure the table has no nullable fields.
                # If the table has nullable fields only checking the table once with instances of
                # one  model class would not necessarily work,
                assert len([f for f in fields if f.null]) == 0
                assert len([f for f in fields if f.related_model == field.related_model]) == 1
                assert len([f for f in fields if f.related_model == model_class]) == 1
                assoc_field = None
                for f in fields:
                    if f.related_model == field.related_model:
                        assoc_field = f
                        break
                else:
                    raise AssertionError()

                current_field = None
                for f in fields:
                    if f.related_model == model_class:
                        current_field = f
                        break
                else:
                    raise AssertionError()
                next_id = get_next_id(through_model)
                # Only the initial data has the many to many relationships annotated to the instances
                assoc_instances = []
                for data in self.parsed_data[model_name]:
                    # serializer has instances of a single model class, i.g. VehicleType.
                    # These need to be translated to the new ids
                    current_instance_id = self.instance_lookup[model_class][data["id"]]
                    # Data[field.name] is an iterable of for example the vehicle classes of
                    # a vehicle type
                    for item in data[field.name]:
                        # new key of imported manytomany instance
                        new_frg_key = self.instance_lookup[field.related_model][item]
                        pk = next_id
                        next_id += 1
                        dict_data = {
                            "id": pk,
                            assoc_field.name + "_id": new_frg_key,
                            current_field.name + "_id": current_instance_id,
                        }
                        assoc_instances.append(through_model(**dict_data))
                through_model.objects.bulk_create(assoc_instances)
                logger.debug(f"{len(assoc_instances)} objects of {through_model} created")


def generate_serializer(model_class: models.Model):
    class Meta:
        model = model_class
        # __all__ includes all fields including ManyToManyRelationships
        fields = "__all__"

    serializer_class = type(
        f"{model_class.__name__}Serializer",
        (serializers.ModelSerializer,),
        {
            "Meta": Meta,
        },
    )
    return serializer_class
