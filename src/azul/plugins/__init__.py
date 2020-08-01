from abc import (
    ABC,
    abstractmethod,
)
import importlib
from inspect import (
    isabstract,
)
from typing import (
    Iterable,
    List,
    Mapping,
    MutableMapping,
    NamedTuple,
    Sequence,
    Type,
    TypeVar,
    Union,
)

from deprecated import (
    deprecated,
)

from azul import (
    CatalogName,
    config,
)
from azul.indexer import (
    Bundle,
    BundleFQID,
)
from azul.indexer.transform import (
    Transformer,
)
from azul.types import (
    JSON,
    JSONs,
    MutableJSONs,
)

ColumnMapping = Mapping[str, str]
MutableColumnMapping = MutableMapping[str, str]
ManifestConfig = Mapping[str, ColumnMapping]
MutableManifestConfig = MutableMapping[str, MutableColumnMapping]
Translation = Mapping[str, str]


class ServiceConfig(NamedTuple):
    # Except otherwise noted the attributes were previously held in a JSON file
    # called `request_config.json`
    translation: Translation
    autocomplete_translation: Mapping[str, Mapping[str, str]]
    manifest: ManifestConfig
    cart_item: Mapping[str, Sequence[str]]
    facets: Sequence[str]
    # This used to be defined in a JSON file called `autocomplete_mapping_config.json`
    autocomplete_mapping_config: Mapping[str, Mapping[str, Union[str, Sequence[str]]]]
    # This used to be defined in a text file called `order_config`
    order_config: Sequence[str]


T = TypeVar('T', bound='Plugin')


class Plugin(ABC):
    """
    A base class for Azul plugins. Concrete plugins shouldn't inherit this
    class directly but one of the subclasses of this class. This class just
    defines the mechanism for loading concrete plugins classes and doesn't
    specify any interface to the concrete plugin itself.
    """

    @classmethod
    def load(cls: Type[T], catalog: CatalogName) -> Type[T]:
        """
        Load and return one of the concrete subclasses of the class this method
        is called on. Which concrete class is returned depends on how the
        catalog is configured. Different catalogs can use different combinations
        of concrete plugin implementations.

        :param catalog: the name of the catalog for which to load the plugin
        """
        assert cls != Plugin, f'Must use a subclass of {cls.__name__}'
        assert isabstract(cls) != Plugin, f'Must use an abstract subclass of {cls.__name__}'
        plugin_type_name = cls._name()
        plugin_package_name = config.plugin_name(catalog, plugin_type_name)
        plugin_package_path = f'{__name__}.{plugin_type_name}.{plugin_package_name}'
        plugin_module = importlib.import_module(plugin_package_path)
        plugin_cls = plugin_module.Plugin
        assert issubclass(plugin_cls, cls)
        return plugin_cls

    @classmethod
    @abstractmethod
    def _name(cls) -> str:
        raise NotImplementedError()


class MetadataPlugin(Plugin):

    @classmethod
    def _name(cls) -> str:
        return 'metadata'

    # If the need arises to parameterize instances of a concrete plugin class,
    # add the parameters to create() and make it abstract.

    @classmethod
    def create(cls) -> 'MetadataPlugin':
        return cls()

    @abstractmethod
    def mapping(self) -> JSON:
        raise NotImplementedError()

    @abstractmethod
    def transformers(self) -> Iterable[Type[Transformer]]:
        raise NotImplementedError()

    @abstractmethod
    def service_config(self) -> ServiceConfig:
        """
        Returns service configuration in a legacy format.
        """
        raise NotImplementedError()


class RepositoryPlugin(Plugin):

    @classmethod
    def _name(cls) -> str:
        return 'repository'

    # If the need arises to parameterize instances of a concrete plugin class,
    # add the parameters to create() and make it abstract.

    @classmethod
    def create(cls) -> 'RepositoryPlugin':
        return cls()

    @property
    @abstractmethod
    def source(self) -> str:
        """
        A string identifiying the source the plugin is configured to read
        metadata from.
        """
        raise NotImplementedError()

    @abstractmethod
    def list_bundles(self, prefix: str) -> List[BundleFQID]:
        raise NotImplementedError()

    @deprecated
    @abstractmethod
    def fetch_bundle_manifest(self, bundle_fqid: BundleFQID) -> MutableJSONs:
        """
        Only used by integration test to filter out bad bundles.

        https://github.com/DataBiosphere/azul/issues/1784 should make this
        unnecessary in DCP/2.

        See Bundle.manifest for the shape of the return value.
        """
        raise NotImplementedError()

    @abstractmethod
    def fetch_bundle(self, bundle_fqid: BundleFQID) -> Bundle:
        raise NotImplementedError()

    @abstractmethod
    def dss_subscription_query(self, prefix: str) -> JSON:
        """
        The query to use for subscribing Azul to bundle additions in the DSS. This query will also be used for
        listing bundles in the DSS during reindexing.

        :param prefix: a prefix that restricts the set of bundles to subscribe to. This parameter is used to subset
                       or partition the set of bundles in the DSS. The returned query should only match bundles whose
                       UUID starts with the given prefix.
        """
        raise NotImplementedError()

    @abstractmethod
    def dss_deletion_subscription_query(self, prefix: str) -> JSON:
        """
        The query to use for subscribing Azul to bundle deletions in the DSS.

        :param prefix: a prefix that restricts the set of bundles to subscribe to. This parameter is used to subset
                       or partition the set of bundles in the DSS. The returned query should only match bundles whose
                       UUID starts with the given prefix.
        """
        raise NotImplementedError()

    @abstractmethod
    def portal_db(self) -> JSONs:
        """
        Returns integrations data object
        """
        raise NotImplementedError()

    @abstractmethod
    def drs_path(self, manifest_entry: JSON, metadata: JSON) -> str:
        """
        Given the manifest entry of a data file and the corresponding metadata
        file, return the file-specific suffix of a DRS URI for that file.

        This method is typically called by the indexer.
        """
        raise NotImplementedError()

    def drs_uri(self, drs_path: str) -> str:
        """
        Given the file-specifc suffix of a DRS URI for a data file, return the
        complete DRS URI.

        This method is typically called by the service.
        """
        return f'drs://{self.drs_netloc()}/{drs_path}'

    def drs_netloc(self) -> str:
        raise NotImplementedError()
