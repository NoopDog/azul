from functools import (
    lru_cache,
)
from typing import (
    Iterable,
    List,
    Tuple,
    Type,
)

from azul import (
    CatalogName,
    config,
)
from azul.indexer.document import (
    Aggregate,
    CataloguedFieldTypes,
    Contribution,
    Document,
    FieldType,
    FieldTypes,
)
from azul.indexer.transform import (
    Transformer,
)
from azul.plugins import (
    MetadataPlugin,
)
from azul.types import (
    AnyJSON,
    AnyMutableJSON,
)


class DocumentService:

    @lru_cache(maxsize=None)
    def metadata_plugin(self, catalog: CatalogName) -> MetadataPlugin:
        return MetadataPlugin.load(catalog).create()

    @lru_cache(maxsize=None)
    def aggregate_class(self, catalog: CatalogName) -> Type[Aggregate]:
        return self.metadata_plugin(catalog).aggregate_class()

    def transformers(self, catalog: CatalogName) -> Iterable[Type[Transformer]]:
        return self.metadata_plugin(catalog).transformers()

    @lru_cache(maxsize=None)
    def entity_types(self, catalog: CatalogName) -> List[str]:
        return [
            transformer.entity_type()
            for transformer in self.transformers(catalog)
        ]

    @lru_cache(maxsize=None)
    def field_type(self, catalog: CatalogName, path: Tuple[str, ...]) -> FieldType:
        """
        Get the type of the field at the given document path.

        :param catalog: The catalog to operate on. Different catalogs may use
                        different field types.

        :param path: A tuple of keys to traverse document.
        """
        field_types = self.field_types(catalog)
        for p in path:
            try:
                field_types = field_types[p]
            except KeyError:
                raise KeyError(f'Path {path} not represented in field_types')
            except TypeError:
                raise TypeError(f'Path {path} not represented in field_types')
            if field_types is None:
                return None
        return field_types

    def field_types(self, catalog: CatalogName) -> FieldTypes:
        """
        Returns a mapping of fields to field types

        :return: dict with nested keys matching Elasticsearch fields and values with the field's type
        """
        field_types = {}
        aggregate_cls = self.aggregate_class(catalog)
        for transformer in self.transformers(catalog):
            field_types.update(transformer.field_types())
        return {
            **Contribution.field_types(field_types),
            **aggregate_cls.field_types(field_types)
        }

    def catalogued_field_types(self) -> CataloguedFieldTypes:
        return {
            catalog: self.field_types(catalog)
            for catalog in config.catalogs
        }

    def translate_fields(self, catalog: CatalogName, doc: AnyJSON, *, forward: bool) -> AnyMutableJSON:
        return Document.translate_fields(doc, self.field_types(catalog), forward=forward)
