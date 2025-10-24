import logging
from copy import copy
from typing import Type

import django.db
import psycopg2
from django.db import models
from django.db.models import Max
from django.db.transaction import atomic
from django.db.utils import ProgrammingError
from django.core.management import call_command
from os import devnull
from django.db import connection
from simba.optimizer_util import time_it
from django.db.models.fields.related import ManyToManyField


class DeepcopyException(Exception):
    pass


def deepcopy_and_sequence_reset(
    instance: models.Model,
    exclude_models: None | set[Type[models.Model]] = None,
    exclude_fields: None | set[Type[models.Field]] = None,
    max_depth=None,
):
    """Deepcopy an object using deepcopy of this module and fix postgres sequences after wards.

    :param instance: object to be copied
    :param exclude_models: models which are skipped during copying
    :param exclude_fields: fields which are skipped during copying
    :param max_depth: maximum recursion depth. For known structures, reducing the max depth
        increases the speed of deep copying.
    :return: copy result instance, stack which links original with copied instances
    """

    copied_instance, deepcopy_locals = deepcopy(
        instance=instance,
        exclude_models=exclude_models,
        exclude_fields=exclude_fields,
        max_depth=max_depth,
    )
    original_copy_dict = deepcopy_locals["stack"]
    reset_postgres_auto_increments(deepcopy_locals["apps"])

    return copied_instance, original_copy_dict


def reset_postgres_auto_increments(apps):
    # Finally fix postgres auto increments for all used apps during this deepcopy
    for app in apps:
        postgres_reset_sql = call_command(
            "sqlsequencereset", app, stdout=open(devnull, "a"), no_color=True
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(postgres_reset_sql)
        except (psycopg2.errors.UndefinedTable, ProgrammingError):
            logging.warning("Undefined table in PostgreSQL: %s", app)


def write_multi_dict(source: dict, keys: list, value):
    """Creates multiple layers of dictionaries if needed and sets values"""
    stem = source
    for key in keys[:-1]:
        try:
            stem[key]
        except KeyError:
            stem[key] = dict()
        stem = stem[key]
    stem[keys[-1]] = value


@time_it
@atomic
def deepcopy(  # noqa
    instance: models.Model,
    exclude_models: None | set[Type[models.Model]] = None,
    exclude_fields: None | set[Type[models.Field]] = None,
    max_depth=None,
):
    """Deepcopy an object and related objects by ForeignKey and ManyToMany Relationship. Requires
    models with 'id' as a primary key with an ascending integer type.
    Does not support multi-tabled inheritance-

    All objects in the database that are connected to the given instance by a ManyToMany
    Relationship or a foreign key are copied. Their references are updated to the set of copied
    instances. Depth of copying can be restricted through field or model exclusion. While the
    exclusion of a model will not copy any related object of that type, the exclusion of a field is
    more restrictive and will only exclude objects which are related through a given field, e.g.,
    ForeignKey or ManyToMany field.
    In both cases, objects that are related to the original instance only through the excluded
    object will be excluded as well

    :param instance: object to be copied
    :param exclude_models: models which are skipped during copying
    :param exclude_fields: fields which are skipped during copying
    :param max_depth: maximum recursion depth. For known structures, reducing the max depth
        increases the speed of deep copying.
    :return: copy result instance, locals of this function
    """

    def _deepcopy(
        object: models.Model,
        stack: dict,
        already_copied: dict,
        copies: dict,
        exclude_models: set,
        exclude_fields: set,
        model_pks: dict,
        max_depth=None,
        current_depth=0,
    ):
        """Recursive deepcopy call

        :param object: object to be copied
        :param stack: reference between old pks and new pks
        :param already_copied: reference of source instances of copying
        :param copies:  reference of result instances of copying
        :param exclude_models: models which are skipped during copying
        :param exclude_fields: fields which are skipped during copying
        :param model_pks: internal counter of pks
        :return: pk of copy result instance
        """

        # Adjust the copies
        skip = True
        # Was this object copied already or is a result of a copy?
        try:
            copies[object.__class__][object.id]
        except KeyError:
            try:
                already_copied[object.__class__][object.id]
            except KeyError:
                skip = False
        if skip:
            return object.pk

        old_pk = object.id
        try:
            model_pks[object.__class__] += 1
        except KeyError:
            model_pks[object.__class__] = (
                object.__class__.objects.aggregate(Max("id"))["id__max"] + 1
            )
        new_pk = model_pks[object.__class__]
        # Create new reference
        copied_obj = copy(object)
        # In cases of inheritance in the models changed pks were not properly saved.
        # Changing to id solved the issue
        # but requires use of "id" as integer pk
        copied_obj.id = new_pk
        #
        write_multi_dict(copies, [object.__class__, new_pk], copied_obj)
        write_multi_dict(already_copied, [object.__class__, old_pk], object)
        write_multi_dict(stack, [object.__class__, old_pk], new_pk)

        if max_depth is not None:
            if current_depth >= max_depth:
                return new_pk

        all_fields = [o for o in object._meta.related_objects]
        many2many = [o for o in object._meta.many_to_many]
        all_fields = [all_fields, many2many]
        for i, fields in enumerate(all_fields):
            for related in fields:
                related_model = related.related_model
                related_field = related.field if i == 0 else related
                if related_field in exclude_fields or related_model in exclude_models:
                    continue
                if i == 0:
                    # which objects point with their foreign key to this instance? Those are related
                    # and need copying
                    rel_objs = related_model.objects.filter(**{related_field.name: old_pk})
                else:
                    # ManyToMany values are accessed through the m2m manager of the object and not
                    # through the model
                    manager = getattr(object, related_field.name)
                    rel_objs = manager.all()
                for o in rel_objs:
                    _deepcopy(
                        o,
                        stack=stack,
                        already_copied=already_copied,
                        copies=copies,
                        exclude_models=exclude_models,
                        exclude_fields=exclude_fields,
                        model_pks=model_pks,
                        max_depth=max_depth,
                        current_depth=current_depth + 1,
                    )
        return new_pk

    # links old_pk-> new_pk for each class
    stack = dict()
    already_copied = dict()
    copies = dict()

    if exclude_models is None:
        exclude_models = {}
    if exclude_fields is None:
        exclude_fields = {}

    # Recursive deepcopy call. Put in wrapper for timing purposes
    new_pk = _deepcopy_wrapper(
        _deepcopy,
        already_copied,
        copies,
        exclude_fields,
        exclude_models,
        instance,
        stack,
        max_depth=max_depth,
    )

    # Revert the stack from old_pk-> new_pk to new_pk->old_pk
    rev_stack = revert_stack(stack)

    apps = {model._meta.app_label for model in copies}
    counter = 0
    while counter < 100:
        counter += 1
        # Bulk create the objects to speed up the writing process
        failed_classes = bulk_create_objects(copies, stack, rev_stack)
        # Replace the foreign keys of the copied objects. This can only be done after they were created
        # to ensure they do not point to not created objects <-- Error
        managers = replace_keys_and_get_managers(
            already_copied, copies, rev_stack, stack, exclude_models, exclude_fields
        )
        # After objects are saved to DB ManyToMany fields can be set
        replace_many2many(managers)

        copies = {key: values for key, values in copies.items() if key in failed_classes}

        if len(copies) == 0:
            break
    else:
        raise Exception("Deepcopying could not create Objects. Database restrictions aren't met?")
    new_pk = instance.pk
    return instance.__class__.objects.get(pk=stack[instance._meta.model][new_pk]), locals()


@time_it
def _deepcopy_wrapper(
    func, already_copied, copies, exclude_fields, exclude_models, instance, stack, max_depth=None
):
    new_pk = func(
        instance,
        stack=stack,
        already_copied=already_copied,
        copies=copies,
        exclude_models=exclude_models,
        exclude_fields=exclude_fields,
        model_pks=dict(),
        max_depth=max_depth,
    )
    return new_pk


@time_it
def revert_stack(stack):
    rev_stack = {}
    for key in stack:
        for key2, value in stack[key].items():
            write_multi_dict(rev_stack, [key, value], key2)
    return rev_stack


@time_it
def bulk_create_objects(copies, stack, rev_stack) -> list:
    @atomic
    def atomic_creation(inner_object_class):
        instances = copies[inner_object_class].values()
        instance_lut = [instance.id for instance in instances]
        # Remove ids so database will handle settting of id/pk
        for instance in instances:
            instance.id = None

        # returned instances have a pk set from the db
        try:
            instances = inner_object_class.objects.bulk_create(instances)
        except Exception:
            # Something failed. restore the pks
            for pk, instance in zip(instance_lut, instances):
                instance.id = pk
            raise

        copies[inner_object_class] = {instance.id: instance for instance in instances}

        # update the stack which links old pks with new pks
        assert len(instances) == len(rev_stack[inner_object_class])
        assert len(instances) == len(stack[inner_object_class])

        for i, instance in enumerate(instances):
            old_pk = rev_stack[inner_object_class][instance_lut[i]]
            stack[inner_object_class][old_pk] = instance.id

        # update the reverse stack which is used later for lookups
        del rev_stack[inner_object_class]
        rev_stack[inner_object_class] = dict()
        for key, value in stack[inner_object_class].items():
            rev_stack[inner_object_class][value] = key

    failed_copies = []
    for object_class in copies:
        try:
            atomic_creation(object_class)
        except django.db.IntegrityError:
            failed_copies.append(object_class)
    return failed_copies


@time_it
def replace_many2many(managers):
    for manager, new_foreign_values in managers:
        manager.add(*new_foreign_values)


@time_it
def replace_keys_and_get_managers(
    already_copied, copies, rev_stack, stack, exclude_models, exclude_fields
):
    managers = list()
    for obj_class in copies:
        if len(copies[obj_class]) == 0:
            continue
        all_copies = [c for c in copies[obj_class].values()]
        fields = obj_class._meta.fields + obj_class._meta.many_to_many
        fnames = []
        for f in fields:
            if not f.related_model or len(copies[obj_class]) == 0:
                continue
            m2m = isinstance(f, ManyToManyField)
            if not m2m:
                fnames.append(f.name)

            # Depending on if it is a m2m field, different functions grab the keys
            get_keys = get_keys_factory(m2m)

            for pk in copies[obj_class]:
                obj_copy = copies[obj_class][pk]
                # Get the foreign key of the original
                org_pk = rev_stack[obj_class][pk]
                # Get the instance of the original
                obj = already_copied[obj_class][org_pk]
                # Grab the primary key(s) of the foreign field
                org_foreign_values = get_keys(obj, f)
                if not m2m:
                    if org_foreign_values is None:
                        continue
                    set_new_foreign_value(
                        f,
                        obj_copy,
                        org_foreign_values,
                        stack,
                        exclude_models=exclude_models,
                        exclude_fields=exclude_fields,
                    )
                else:
                    managers.append(
                        create_m2m_managers(
                            f,
                            obj_copy,
                            org_foreign_values,
                            stack,
                            exclude_models=exclude_models,
                            exclude_fields=exclude_fields,
                        )
                    )
        if len(fnames) > 0:
            try:
                obj_class.objects.fast_update(all_copies, fnames)
            except AttributeError:
                obj_class.objects.bulk_update(all_copies, fnames)
    return managers


def get_keys_factory(m2m):
    if m2m:

        def get_keys(obj, f):
            return getattr(obj, f.name).all()

    else:

        def get_keys(obj, f):
            return obj.__dict__.get(f.name + "_id")

    return get_keys


def create_m2m_managers(f, obj_copy, org_foreign_values, stack, exclude_models, exclude_fields):
    new_foreign_values = []
    # Replace all foreign keys with the copy/pk translation of the objects
    for old_foreign in org_foreign_values:
        try:
            new_foreign_values.append(stack[f.related_model][old_foreign.pk])
        except KeyError:
            if f in exclude_fields or f.related_model in exclude_models:
                new_foreign_values = org_foreign_values
                break
            else:
                raise DeepcopyException(f"No copy found for id:{old_foreign.pk} of field:{f}")
    manager = getattr(obj_copy, f.name)
    return manager, new_foreign_values


def set_new_foreign_value(f, obj_copy, org_foreign_values, stack, exclude_models, exclude_fields):
    try:
        new_foreign_values = stack[f.related_model][org_foreign_values]
        setattr(obj_copy, f.name + "_id", new_foreign_values)
    except KeyError:
        if f in exclude_fields or f.related_model in exclude_models:
            pass
        else:
            raise DeepcopyException(f"No copy found for id:{org_foreign_values} of field:{f}")
