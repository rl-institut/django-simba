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
from django.db.models.fields.related import ManyToManyField

logger = logging.getLogger("custom")


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
    :return: copy result instance, stack_pre which links original with copied instances
    """

    copied_instance, deepcopy_locals = deepcopy(
        instance=instance,
        exclude_models=exclude_models,
        exclude_fields=exclude_fields,
        max_depth=max_depth,
    )
    original_copy_dict = deepcopy_locals["stack_post"]

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
            logging.debug("Undefined table in PostgreSQL: %s", app)


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

    old_id = instance.id

    def _deepcopy(
        object: models.Model,
        stack_pre: dict,
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
        :param stack_pre: reference between old pks and new pks
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
        write_multi_dict(stack_pre, [object.__class__, old_pk], new_pk)

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
                        stack_pre=stack_pre,
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
    stack_pre = dict()
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
        stack_pre,
        max_depth=max_depth,
    )

    # Revert the stack_pre from old_pk-> new_pk to new_pk->old_pk
    # pre stands for linkage BEFORE creation.
    # The final pks are set by the db during bulk creation.
    # the final linkage between objects is found in the stack_post rev_stack_post dicts
    rev_stack_pre = revert_stack(stack_pre)

    stack_post = {}
    rev_stack_post = {}
    already_updated_fields = {}
    managers = []

    counter = 0
    _copies = {key: value for key, value in copies.items()}
    # Allow 10 retries
    while _copies and counter < 10:
        counter += 1
        # Bulk create the objects to speed up the writing process
        logger.debug("bulk creating")
        # Mutates all stacks in place
        failed_classes = bulk_create_objects(
            _copies, stack_pre, rev_stack_pre, stack_post, rev_stack_post
        )
        suceeded_classes = set(_copies.keys()).difference(failed_classes)

        # Replace the foreign keys of the all objects, when their foreign key model has been created
        logger.debug("updating copies with new foreign keys")
        managers.extend(
            replace_keys_and_get_managers(
                already_copied,
                _copies,
                rev_stack_post,
                stack_post,
                rev_stack_pre,
                exclude_models,
                exclude_fields,
                already_updated_fields,
            )
        )

        _copies = {key: values for key, values in _copies.items() if key in failed_classes}
        if len(_copies) == 0:
            break
    else:
        raise Exception("Deepcopying could not create Objects. Database restrictions aren't met?")

    # Make sure to iterate of copies and not the filtered _copies version
    # At this point all instances are created. Update fields which might have not been updated before
    logger.debug("Check for missing updates")
    managers.extend(
        replace_keys_and_get_managers(
            already_copied,
            copies,
            rev_stack_post,
            stack_post,
            rev_stack_pre,
            exclude_models,
            exclude_fields,
            already_updated_fields,
        )
    )

    # After objects are saved to DB ManyToMany fields can be set
    logger.debug("setting new many to many")
    replace_many2many(managers)

    for model_class, objs in copies.items():
        logger.debug(f"bulk updating copies with new foreign keys for {model_class}")
        fnames = [
            f.name
            for f in already_updated_fields[model_class]
            if not isinstance(f, ManyToManyField)
        ]
        if not fnames:
            logger.debug(f"skipped {model_class} since no fields were updated")
            continue
        try:
            model_class.objects.fast_update(objs.values(), fnames)
        except AttributeError:
            model_class.objects.bulk_update(objs.values(), fnames)

    apps = {model._meta.app_label for model in copies}
    return instance.__class__.objects.get(pk=stack_post[instance._meta.model][old_id]), locals()


def _deepcopy_wrapper(
    func,
    already_copied,
    copies,
    exclude_fields,
    exclude_models,
    instance,
    stack_pre,
    max_depth=None,
):
    new_pk = func(
        instance,
        stack_pre=stack_pre,
        already_copied=already_copied,
        copies=copies,
        exclude_models=exclude_models,
        exclude_fields=exclude_fields,
        model_pks=dict(),
        max_depth=max_depth,
    )
    return new_pk


def revert_stack(stack):
    rev_stack = {}
    for key in stack:
        for key2, value in stack[key].items():
            write_multi_dict(rev_stack, [key, value], key2)
    return rev_stack


def bulk_create_objects(copies, stack_pre, rev_stack_pre, stack_post, rev_stack_post) -> list:
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
        assert len(instances) == len(rev_stack_pre[inner_object_class])
        assert len(instances) == len(stack_pre[inner_object_class])

        assert stack_post.get(inner_object_class) is None
        stack_post[inner_object_class] = dict()
        for i, instance in enumerate(instances):
            old_pk = rev_stack_pre[inner_object_class][instance_lut[i]]
            stack_post[inner_object_class][old_pk] = instance.id

        # update the reverse stack which is used later for lookups
        rev_stack_post[inner_object_class] = dict()
        rev_stack_post[inner_object_class] = dict()
        for key, value in stack_post[inner_object_class].items():
            rev_stack_post[inner_object_class][value] = key

    failed_copies = []
    for object_class in copies:
        logger.debug(f"trying {object_class}")
        try:
            atomic_creation(object_class)
        except django.db.IntegrityError:
            failed_copies.append(object_class)
            logger.debug(f"failed {object_class}")

    return failed_copies


def replace_many2many(managers):
    for manager, new_foreign_values in managers:
        logger.debug(manager)
        manager.add(*new_foreign_values)


def replace_keys_and_get_managers(
    already_copied,
    copies,
    rev_stack_post,
    stack_post,
    rev_stack_pre,
    exclude_models,
    exclude_fields,
    already_updated_fields,
):
    managers = list()
    filtered_copies = {key: value for key, value in copies.items() if not len(value) == 0}
    for obj_class in filtered_copies:
        if obj_class not in already_updated_fields:
            already_updated_fields[obj_class] = set()
        updated_fields = already_updated_fields[obj_class]

        fields = obj_class._meta.fields + obj_class._meta.many_to_many
        # Only update fields if they have are have a related model, e.g. a foreignkey or many to many
        # Only update fields which have not been updated before
        # Only update fields, if the related model has been bulk created yet
        filtered_fields = [
            f
            for f in fields
            if f.related_model and f not in updated_fields and f.related_model in stack_post
        ]
        for f in filtered_fields:
            logger.debug(f"Updating {f} in {obj_class}")
            # Keep track of this field, so its not updated twice
            updated_fields.add(f)
            m2m = isinstance(f, ManyToManyField)

            # Depending on if it is a m2m field, different functions grab the keys
            get_keys = get_keys_factory(m2m)

            for pk, obj_copy in copies[obj_class].items():
                # Get the pk key of the original. ManyToManyFields are not stored in the
                # copied instance but accessible through the manager.
                # We need the original instance to look up the proper  manager
                if obj_class in rev_stack_post:
                    org_pk = rev_stack_post[obj_class][pk]
                else:
                    org_pk = rev_stack_pre[obj_class][pk]
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
                        stack_post,
                        exclude_models=exclude_models,
                        exclude_fields=exclude_fields,
                    )
                else:
                    manager = create_m2m_managers(
                        f,
                        obj_copy,
                        org_foreign_values,
                        stack_post,
                        exclude_models=exclude_models,
                        exclude_fields=exclude_fields,
                    )
                    # Actively managed through models dont need to be handled through managers
                    # They are handled through foreign fields of the through model instances
                    if manager[0].through in copies or manager[0].through in stack_post:
                        logger.debug(f"skipping {f} manager for {obj_class} ")
                        continue

                    logger.debug(f"appending {f} manager for { obj_class } ")
                    managers.append(manager)
    return managers


def get_keys_factory(m2m):
    if m2m:

        def get_keys(obj, f):
            return getattr(obj, f.name).all()

    else:

        def get_keys(obj, f):
            return obj.__dict__.get(f.name + "_id")

    return get_keys


def create_m2m_managers(
    f, obj_copy, org_foreign_values, stack_post, exclude_models, exclude_fields
):
    new_foreign_values = []
    # Replace all foreign keys with the copy/pk translation of the objects
    for old_foreign in org_foreign_values:
        try:
            new_foreign_values.append(stack_post[f.related_model][old_foreign.pk])
        except KeyError:
            if f in exclude_fields or f.related_model in exclude_models:
                new_foreign_values = org_foreign_values
                break
            else:
                raise DeepcopyException(f"No copy found for id:{old_foreign.pk} of field:{f}")
    manager = getattr(obj_copy, f.name)
    return manager, new_foreign_values


def set_new_foreign_value(
    f, obj_copy, org_foreign_values, stack_post, exclude_models, exclude_fields
):
    try:
        new_foreign_values = stack_post[f.related_model][org_foreign_values]
        setattr(obj_copy, f.name + "_id", new_foreign_values)
    except KeyError:
        if f in exclude_fields or f.related_model in exclude_models:
            pass
        else:
            raise DeepcopyException(f"No copy found for id:{org_foreign_values} of field:{f}")
