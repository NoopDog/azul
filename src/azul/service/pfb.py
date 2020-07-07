from collections import (
    defaultdict,
)
import logging
from typing import (
    Iterable,
    MutableMapping,
    MutableSet,
    cast,
)
import uuid

import attr
import fastavro
from fastavro.validation import (
    ValidationError,
)
from more_itertools import (
    one,
)

from azul import (
    config,
)
from azul.indexer.document import (
    FieldTypes,
    null_bool,
    null_float,
    null_int,
    null_int_sum_sort,
    null_str,
    pass_thru_int,
    pass_thru_json,
)
from azul.json_freeze import (
    freeze,
    sort_frozen,
)
from azul.plugins.metadata.hca.transform import (
    pass_thru_uuid4,
    value_and_unit,
)
from azul.types import (
    JSON,
    MutableJSON,
)

log = logging.getLogger(__name__)


@attr.s(auto_attribs=True, frozen=True, kw_only=True)
class Relation:
    dst_id: str
    # A more appropriate variable name would be dst_type, but we stick with name
    # to conform to PFB spec
    dst_name: str

    @classmethod
    def to_entity(cls, entity: 'PFBEntity'):
        return cls(dst_id=entity.id, dst_name=entity.name)


pfb_namespace = uuid.UUID('3372fc62-76d2-4ada-a68a-f08d4f558da5')


@attr.s(auto_attribs=True, frozen=True, kw_only=True)
class PFBEntity:
    """
    Python representation of the PFB data object. Attribute names conform to the
    PFB spec (which simplifies serialization).
    """
    id: str
    name: str
    object: MutableJSON = attr.ib(eq=False)

    @classmethod
    def from_no_id(cls, name: str, object_: MutableJSON) -> 'PFBEntity':
        """
        Derive id from object in a reproducible way so that we can distinguish
        entities by comparing their ids.

        Entities need to be quickly distinguishable, but unfortunately
        document_id wasn't unique (or present) for all entities. Thus, we use
        the frozen state of the JSON to generate our hash and check equality.
        """
        entity_id = str(uuid.uuid5(pfb_namespace, repr(sort_frozen(freeze(object_)))))
        return cls(id=entity_id, name=name, object=object_)

    def to_json(self, relations: Iterable[Relation]):
        return {
            **attr.asdict(self),
            "relations": [attr.asdict(relation) for relation in relations]
        }


class EntityAggregator:

    def __init__(self):
        self._entities_to_relations: MutableMapping[PFBEntity, MutableSet[Relation]] = defaultdict(set)

    def add_doc(self, doc: JSON):
        """
        For an Elasticsearch document
        """
        contents = doc['contents']
        # File entities are assumed to be unique
        file_entity = PFBEntity.from_no_id(name='files', object_=one(contents['files']))
        assert file_entity not in self._entities_to_relations
        # File entities won't relate *to* anything, but things relate *to* them.
        # Terra streams PFBs and thus entities must be defined before they are
        # referenced. This is one reason that file_entity is added first.
        self._entities_to_relations[file_entity] = set()
        for entity_type, objects in contents.items():
            # FIXME: Checking the type here is working around the unexpected
            # attribute contents.total_estimated_cells
            #  https://github.com/DataBiosphere/azul/issues/2298
            if isinstance(objects, list):
                if entity_type != 'files':
                    if len(objects) == 0:
                        continue
                    elif len(objects) == 1:
                        entity = PFBEntity.from_no_id(name=entity_type, object_=one(objects))
                        self._entities_to_relations[entity].add(Relation.to_entity(file_entity))
                    else:
                        raise ValueError(f'Elasticsearch document {doc} had unexpected {entity_type!r} entities')

    def entities(self, schema: JSON) -> Iterable[PFBEntity]:
        for entity, relations in self._entities_to_relations.items():
            normalize(entity, schema)
            replace_null_with_empty_string(entity.object)
            yield entity.to_json(relations)


def normalize(entity: PFBEntity, schema):
    """
    Compare entities against the schema and add any fields that are missing.

    None is the default value, but because of https://github.com/DataBiosphere/azul/issues/2370
    this isn't currently reflected in the schema.
    """
    object_schema = one(f for f in schema['fields'] if f['name'] == 'object')
    entity_schema = one(e for e in object_schema['type'] if e['name'] == entity.name)
    fields = entity_schema['fields']
    for field in fields:
        if field['name'] not in entity.object:
            if entity.name == 'files':
                default_value = None
            else:
                assert field['type']['type'] == 'array'
                # Default for a list of records is an empty list
                if isinstance(field['type']['items'], dict):
                    assert field['type']['items']['type'] == 'record'
                    default_value = []
                # All other types are lists of primitives where None is an accepted value
                else:
                    # Change 'string' to 'null' in https://github.com/DataBiosphere/azul/issues/2462
                    assert 'string' in field['type']['items']
                    default_value = [None]
            entity.object[field['name']] = default_value


def replace_null_with_empty_string(object_json: MutableJSON):
    # FIXME remove with https://github.com/DataBiosphere/azul/issues/2462
    for k, v in object_json.items():
        if isinstance(v, dict):
            replace_null_with_empty_string(v)
        elif v is None:
            object_json[k] = ''
        elif v == [None]:
            object_json[k] = ['']


def write_entities(entities: Iterable[JSON], json_schema: JSON, path: str):
    # fastavro doesn't know about our JSON type, hence the cast
    parsed_schema = fastavro.parse_schema(cast(dict, json_schema))
    with open(path, 'w+b') as fh:
        # Writing the entities one at a time is ~2.5 slower, but makes it clear
        # which entities fail, which is useful for debugging.
        if config.debug:
            log.info('Writing PFB entities individually')
            for entity in entities:
                try:
                    fastavro.writer(fh, parsed_schema, [entity], validator=True)
                except ValidationError:
                    log.error('Failed to write Avro entity: %r', entity)
                    raise
        else:
            fastavro.writer(fh, parsed_schema, entities, validator=True)


def pfb_schema(encoded_schema: Iterable[JSON]) -> JSON:
    return {
        "type": "record",
        "name": "Entity",
        "fields": [
            {"name": "id", "type": ["null", "string"], "default": None},
            {"name": "name", "type": "string"},
            {
                "name": "object",
                "type": [
                    {
                        "type": "record",
                        "name": "Metadata",
                        "fields": [
                            {
                                "name": "nodes",
                                "type": {
                                    "type": "array",
                                    "items": {
                                        "type": "record",
                                        "name": "Node",
                                        "fields": [
                                            {"name": "name", "type": "string"},
                                            {
                                                "name": "ontology_reference",
                                                "type": "string",
                                            },
                                            {
                                                "name": "values",
                                                "type": {
                                                    "type": "map",
                                                    "values": "string",
                                                },
                                            },
                                            {
                                                "name": "links",
                                                "type": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "record",
                                                        "name": "Link",
                                                        "fields": [
                                                            {
                                                                "name": "multiplicity",
                                                                "type": {
                                                                    "type": "enum",
                                                                    "name": "Multiplicity",
                                                                    "symbols": [
                                                                        "ONE_TO_ONE",
                                                                        "ONE_TO_MANY",
                                                                        "MANY_TO_ONE",
                                                                        "MANY_TO_MANY",
                                                                    ],
                                                                },
                                                            },
                                                            {
                                                                "name": "dst",
                                                                "type": "string",
                                                            },
                                                            {
                                                                "name": "name",
                                                                "type": "string",
                                                            },
                                                        ],
                                                    },
                                                },
                                            },
                                            {
                                                "name": "properties",
                                                "type": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "record",
                                                        "name": "Property",
                                                        "fields": [
                                                            {
                                                                "name": "name",
                                                                "type": "string",
                                                            },
                                                            {
                                                                "name": "ontology_reference",
                                                                "type": "string",
                                                            },
                                                            {
                                                                "name": "values",
                                                                "type": {
                                                                    "type": "map",
                                                                    "values": "string",
                                                                },
                                                            },
                                                        ],
                                                    },
                                                },
                                            },
                                        ],
                                    },
                                },
                            },
                            {
                                "name": "misc",
                                "type": {"type": "map", "values": "string"},
                            },
                        ],
                    },
                    *encoded_schema
                ]
            },
            {
                "name": "relations",
                "type": {
                    "type": "array",
                    "items": {
                        "type": "record",
                        "name": "Relation",
                        "fields": [
                            {"name": "dst_id", "type": "string"},
                            {"name": "dst_name", "type": "string"},
                        ],
                    },
                },
                "default": [],
            },
        ],
    }


def tables_schema(field_types: FieldTypes) -> Iterable[JSON]:
    """
    Produce a sequence Avro schemas for each inner entity given a FieldTypes
    mapping.

    :param field_types: Dictionary structure mapping the ES doc structure to
        types.
    :return: A generator of schemas
    """
    for entity_type, field_types in field_types.items():
        # FIXME: Checking the type here is working around the unexpected
        # attribute contents.total_estimated_cells
        #  https://github.com/DataBiosphere/azul/issues/2298
        if isinstance(field_types, dict):
            yield {
                "name": entity_type,
                "type": "record",
                "fields": list(_tables_schema_recursive(field_types, singleton=entity_type == 'files'))
            }


nullable_to_pfb_types = {
    null_bool: ['string', 'boolean'],
    null_float: ['string', 'double'],  # Not present in current field_types
    null_int: ['string', 'long'],
    null_str: ['string'],
    null_int_sum_sort: ['string', 'long']
}


def _tables_schema_recursive(field_types: FieldTypes, singleton: bool = False, depth=0) -> Iterable[JSON]:
    for entity_type, field_types in field_types.items():
        if isinstance(field_types, dict):
            yield {
                "name": entity_type,
                "type": {
                    # This is always an array, even if singleton is passed in
                    "type": "array",
                    "items": {
                        "name": entity_type,
                        "type": "record",
                        "fields": list(_tables_schema_recursive(field_types, depth=depth + 1))
                    }
                }
            }
        elif field_types in nullable_to_pfb_types:
            # Files fields from the files index are not wrapped in list
            # FIXME: Why is content_description a list while other file fields are not?
            file_entity_field = singleton and entity_type != 'content_description'
            # FIXME: Remove exception for total_estimated_cells when field is moved
            #  https://github.com/DataBiosphere/azul/issues/2298
            if file_entity_field or (entity_type == 'total_estimated_cells') or depth > 0:
                yield {
                    "name": entity_type,
                    "type": list(nullable_to_pfb_types[field_types]),
                }
            else:
                yield {
                    "name": entity_type,
                    "type": {
                        "type": "array",
                        "items": list(nullable_to_pfb_types[field_types]),
                    }
                }
        elif field_types is pass_thru_uuid4:
            yield {
                "name": entity_type,
                "default": None,
                # FIXME: Why does pass_thru_uuid appear in documents when other pass_throughs don't?
                "type": ["string"],
                "logicalType": "UUID"
            }
        elif field_types is value_and_unit:
            yield {
                "name": entity_type,
                "type": {
                    "name": entity_type,
                    "type": "array",
                    "items": [
                        # Change 'string' to 'null' in https://github.com/DataBiosphere/azul/issues/2462
                        "string",
                        {
                            "name": entity_type,
                            "type": "record",
                            "fields": [
                                {
                                    "name": name,
                                    # Although, not technically a null_str, it's effectively the same
                                    "type": nullable_to_pfb_types[null_str]
                                } for name in ('value', 'unit')
                            ]
                        }
                    ]
                }
            }
        elif field_types in (pass_thru_json, pass_thru_int):
            # Pass thru types are used only for aggregation and are excluded
            # from actual hits
            continue
        else:
            raise ValueError(f'Unexpected type in schema: {field_types!r}')


def metadata_entity(field_types: FieldTypes):
    """
    The Metadata entity encodes the possible relationships between tables.

    Unfortunately Terra does not display the relations between the nodes.
    """
    return {
        "id": None,
        "name": "Metadata",
        "object": {
            "nodes": [
                {
                    "name": field_type,
                    "ontology_reference": "",
                    "values": {},
                    "links": [] if field_type == 'files' else [{
                        "multiplicity": "MANY_TO_MANY",
                        "dst": "files",
                        "name": "files"
                    }],
                    "properties": []
                } for field_type in field_types
            ],
            "misc": {}
        }
    }
