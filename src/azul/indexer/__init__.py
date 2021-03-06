from abc import (
    ABC,
    abstractmethod,
)
from typing import (
    NamedTuple,
    Optional,
)

import attr

from azul.types import (
    JSON,
    MutableJSON,
    MutableJSONs,
)

BundleUUID = str
BundleVersion = str


class BundleFQID(NamedTuple):
    uuid: BundleUUID
    version: BundleVersion


@attr.s(auto_attribs=True, kw_only=True)
class Bundle(ABC):
    uuid: BundleUUID
    version: BundleVersion
    manifest: MutableJSONs
    """
    Each item of the `manifest` attribute's value has this shape:
    {
        'content-type': 'application/json; dcp-type="metadata/biomaterial"',
        'crc32c': 'fd239631',
        'indexed': True,
        'name': 'cell_suspension_0.json',
        's3_etag': 'aa31c093cc816edb1f3a42e577872ec6',
        'sha1': 'f413a9a7923dee616309e4f40752859195798a5d',
        'sha256': 'ea4c9ed9e53a3aa2ca4b7dffcacb6bbe9108a460e8e15d2b3d5e8e5261fb043e',
        'size': 1366,
        'uuid': '0136ebb4-1317-42a0-8826-502fae25c29f',
        'version': '2019-05-16T162155.020000Z'
    }
    """
    metadata_files: MutableJSON

    @classmethod
    def for_fqid(cls, fqid: BundleFQID, *, manifest: MutableJSONs, metadata_files: MutableJSON) -> 'Bundle':
        uuid, version = fqid
        return cls(uuid=uuid,
                   version=version,
                   manifest=manifest,
                   metadata_files=metadata_files)

    @property
    def fquid(self):
        return BundleFQID(self.uuid, self.version)

    @abstractmethod
    def drs_path(self, manifest_entry: JSON) -> Optional[str]:
        """
        Return the path component of a DRS URI to a data file in this bundle,
        or None if the data file is not accessible via DRS.

        :param manifest_entry: the manifest entry of the data file.
        """
        raise NotImplementedError
