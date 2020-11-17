import argparse
from collections import (
    defaultdict,
)
import copy
from datetime import (
    datetime,
)
from enum import (
    Enum,
)
import json
import os
import sys
from typing import (
    Dict,
    Iterable,
    List,
    Optional,
)
import uuid

from furl import (
    furl,
)
from more_itertools import (
    one,
)

from azul import (
    config,
    dss,
    logging,
)
from azul.indexer import (
    Bundle,
)
from azul.indexer.document import (
    EntityID,
    EntityType,
)
from azul.json import (
    copy_json,
    copy_jsons,
)
from azul.logging import (
    configure_script_logging,
)
from azul.plugins.repository import (
    tdr,
)
from azul.plugins.repository.dss import (
    DSSBundle,
)
from azul.plugins.repository.tdr import (
    TDRBundle,
)
from azul.terra import (
    TDRSource,
)
from azul.types import (
    JSON,
    MutableJSONs,
)

log = logging.getLogger(__name__)


class TestTables(Enum):
    snapshot = 'snapshot'
    dataset = 'dataset'
    supp_files = 'snapshot_supp_files'

    @classmethod
    def values(cls) -> List[str]:
        return [t.value for t in cls]


def file_paths(parent_dir: str,
               bundle_uuid: str
               ):
    def paths(*parts: str, ext: str = ''):
        return {part: os.path.join(parent_dir, f'{bundle_uuid}.{part}{ext}.json') for part in parts}

    return {
        'dss': paths('manifest', 'metadata'),
        'tdr': {
            t.value: paths('manifest', 'metadata', 'bigquery', ext=f'.{t.value}.tdr')
            for t in TestTables
        }
    }


def find_concrete_type(bundle: Bundle, entity_id: EntityID):
    entity_key = one(k for k, v in bundle.metadata_files.items()
                     if k != 'links.json' and v['provenance']['document_id'] == entity_id)
    return bundle.concrete_entity_type(entity_key)


def convert_version(version: str) -> str:
    return datetime.strptime(
        version,
        dss.version_format
    ).strftime(
        tdr.Plugin.timestamp_format
    )


def content_length(content: JSON) -> int:
    return len(json.dumps(content).encode('UTF-8'))


def random_uuid() -> str:
    return str(uuid.uuid4())


def drs_path(snapshot_id: str, file_id: str) -> str:
    return f'v1_{snapshot_id}_{file_id}'


def drs_uri(drs_path: Optional[str]) -> Optional[str]:
    if drs_path is None:
        return None
    else:
        netloc = furl(config.tdr_service_url).netloc
        return f'drs://{netloc}/{drs_path}'


def dss_bundle_to_tdr(bundle: Bundle, source: TDRSource, source_uuid: str) -> TDRBundle:
    metadata = copy_json(bundle.metadata_files)
    links_json = metadata['links.json']
    links_json['schema_type'] = 'links'  # DCP/1 uses 'link_bundle'
    for link in links_json['links']:
        process_id = link.pop('process')
        link['process_id'] = process_id
        link['process_type'] = find_concrete_type(bundle, process_id)
        link['link_type'] = 'process_link'  # No supplementary files in DCP/1 bundles
        for component in ('input', 'output'):  # Protocols already in desired format
            del link[f'{component}_type']  # Replace abstract type with concrete types
            component_list = link[f'{component}s']
            component_list[:] = [
                {
                    f'{component}_id': component_id,
                    f'{component}_type': find_concrete_type(bundle, component_id)
                }
                for component_id in component_list
            ]

    manifest: MutableJSONs = copy_jsons(bundle.manifest)
    links_entry = None
    for entry in manifest:
        entry['version'] = convert_version(entry['version'])
        if entry['name'] == 'links.json':
            links_entry = entry
        if entry['indexed']:
            entity_json = metadata[entry['name']]
            # Size of the entity JSON in TDR, not the size of pretty-printed
            # output file.
            entry['size'] = content_length(entity_json)
            # Only include mandatory checksums
            del entry['sha1']
            del entry['s3_etag']
            entry['crc32c'] = ''
            entry['sha256'] = ''
        else:
            # Generate random DRS ID
            entry['drs_path'] = drs_path(source_uuid, entry['uuid'])

    assert links_entry is not None
    # links.json has no FQID of its own in TDR since its FQID is used
    # for the entire bundle.
    links_entry['uuid'] = bundle.uuid
    return TDRBundle(uuid=links_entry['uuid'],
                     version=links_entry['version'],
                     source=source,
                     manifest=manifest,
                     metadata_files=metadata)


class Entity:

    def __init__(self, bundle: TDRBundle, entity_key: str):
        self.concrete_type = bundle.concrete_entity_type(entity_key)
        self.manifest_entry = one(e for e in bundle.manifest if e['name'] == entity_key)
        self.metadata = bundle.metadata_files[entity_key]

    def to_json_row(self) -> JSON:
        return {
            f'{self.concrete_type}_id': self.manifest_entry['uuid'],
            'version': self.manifest_entry['version'],
            'content': json.dumps(self.metadata)
        }

    def create_older_version(self, *, version_decrement: int) -> 'Entity':
        predecessor = copy.deepcopy(self)
        predecessor.manifest_entry['version'] = self._earlier_version(predecessor.manifest_entry['version'],
                                                                      version_decrement)
        return predecessor

    def _earlier_version(self, version: str, dec: int) -> str:
        year, rest = version.split('-', 1)
        year = int(year) - dec
        assert year > 0
        return f'{year:04}-{rest}'


class Links(Entity):

    def __init__(self, bundle: TDRBundle, entity_key: str):
        assert entity_key == 'links.json'
        super().__init__(bundle, entity_key),
        self.project_id = bundle.metadata_files['project_0.json']['provenance']['document_id']

    def to_json_row(self) -> JSON:
        return dict(super().to_json_row(),
                    project_id=self.project_id)


class File(Entity):

    def __init__(self, bundle: TDRBundle, entity_key: str):
        super().__init__(bundle, entity_key)
        assert self.concrete_type.endswith('_file')
        self.file_manifest_entry = one(e for e in bundle.manifest
                                       if e['name'] == self.metadata['file_core']['file_name'])
        if bundle.source.is_snapshot:
            assert self.file_manifest_entry['drs_path'] is not None
        else:
            self.file_manifest_entry['drs_path'] = None

    def to_json_row(self) -> JSON:
        return dict(super().to_json_row(),
                    file_id=drs_uri(self.file_manifest_entry['drs_path']),
                    descriptor=json.dumps(dict(tdr.Checksums.from_json(self.file_manifest_entry).to_json(),
                                               file_name=self.file_manifest_entry['name'],
                                               file_version=self.file_manifest_entry['version'],
                                               file_id=self.file_manifest_entry['uuid'],
                                               content_type=self.file_manifest_entry['content-type'].split(';', 1)[0],
                                               size=self.file_manifest_entry['size'])))


def collect_entities(bundle: TDRBundle) -> Dict[EntityType, List[Entity]]:
    tables = defaultdict(list)
    for entity_key in bundle.metadata_files:
        if entity_key == 'links.json':
            entity_cls = Links
        elif '_file_' in entity_key:
            entity_cls = File
        else:
            entity_cls = Entity
        entity = entity_cls(bundle, entity_key)
        tables[entity.concrete_type].append(entity)
    return tables


def add_older_versions(entities: Iterable[Entity], *, num_older: int = 1) -> Iterable[Entity]:
    for entity in entities:
        yield entity
        for i in range(1, num_older + 1):
            yield entity.create_older_version(version_decrement=i)


def add_supp_files(bundle: TDRBundle, source_uuid: str, *, num_files: int) -> TDRBundle:
    bundle = copy.deepcopy(bundle)
    links_json = bundle.metadata_files['links.json']['links']
    links_manifest = one(e for e in bundle.manifest if e['name'] == 'links.json')
    project_id = bundle.metadata_files['project_0.json']['provenance']['document_id']

    for i in range(num_files):
        metadata_id = random_uuid()
        data_id = random_uuid()
        drs_id = data_id
        document_name = f'supplementary_file_{i}.json'
        file_name = f'{metadata_id}_file_name.fmt'
        version = datetime.now().strftime(tdr.Plugin.timestamp_format)
        content = {
            'describedBy': '.../supplementary_file',
            'schema_type': 'file',
            'provenance': {
                'document_id': metadata_id
            },
            'file_core': {
                'file_name': file_name
            }
        }
        bundle.metadata_files[document_name] = content
        bundle.manifest.extend([{
            'name': document_name,
            'uuid': metadata_id,
            'version': version,
            'size': content_length(content),
            'indexed': True,
            'content-type': 'application/json; dcp-type="metadata/file"',
            'crc32c': '',
            'sha256': ''
        }, {
            'name': file_name,
            'uuid': data_id,
            'size': 1024,
            'indexed': False,
            'content-type': 'whatever format there are in; dcp-type=data',
            'version': version,
            'crc32c': '',
            'sha256': '',
            'drs_path': drs_path(source_uuid, drs_id)
        }])
        links_json.append({
            'link_type': 'supplementary_file_link',
            'entity': {
                'entity_type': 'project',
                'entity_id': project_id
            },
            'files': [{
                'file_type': 'supplementary_file',
                'file_id': metadata_id
            }]
        })
    # Update size in manifest to include new links
    links_manifest['size'] = content_length(bundle.metadata_files['links.json'])
    return bundle


def main(argv):
    """
    Load a canned bundle from DCP/1 and write *.manifest.tdr and *.metadata.tdr
    files showing the desired output for DCP/2.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle-uuid', '-b',
                        help='The UUID of the existing DCP/1 canned bundle.')
    parser.add_argument('--source-uuid', '-s',
                        help='The UUID of the snapshot/dataset to contain the canned DCP/2 bundle.')
    parser.add_argument('--input-dir', '-I',
                        default=os.path.join(config.project_root, 'test', 'indexer', 'data'),
                        help='The path to the input directory (default: %(default)s).')
    args = parser.parse_args(argv)

    paths = file_paths(args.input_dir, args.bundle_uuid)

    log.debug('Reading canned bundle %r from %r', args.bundle_uuid, paths['dss'])
    with open(paths['dss']['manifest']) as f:
        manifest = json.load(f)
    with open(paths['dss']['metadata']) as f:
        metadata = json.load(f)

    dss_bundle = DSSBundle(uuid=args.bundle_uuid,
                           version='',
                           manifest=manifest,
                           metadata_files=metadata)

    snapshot_source = TDRSource(project='test_project',
                                name='test_name',
                                is_snapshot=True)
    dataset_source = TDRSource(project='test_project',
                               name='test_name',
                               is_snapshot=False)

    snapshot_bundle = dss_bundle_to_tdr(dss_bundle, snapshot_source, args.source_uuid)
    dataset_bundle = dss_bundle_to_tdr(dss_bundle, dataset_source, args.source_uuid)
    supp_files_bundle = add_supp_files(snapshot_bundle, args.source_uuid, num_files=5)

    bundles = {
        TestTables.snapshot: (snapshot_bundle, collect_entities(snapshot_bundle)),
        TestTables.dataset: (dataset_bundle, {entity_type: add_older_versions(entities, num_older=2)
                                              for entity_type, entities in collect_entities(dataset_bundle).items()}),
        TestTables.supp_files: (supp_files_bundle, collect_entities(supp_files_bundle))
    }

    log.debug('Writing converted bundle %r to %r', args.bundle_uuid, paths['tdr'])
    for t in TestTables:
        test_paths = paths['tdr'][t.value]
        bundle, entities = bundles[t]
        with open(test_paths['manifest'], 'w') as f:
            json.dump(bundle.manifest, f, indent=4)
        with open(test_paths['metadata'], 'w') as f:
            json.dump(bundle.metadata_files, f, indent=4)
        with open(test_paths['bigquery'], 'w') as f:
            json.dump({et: [e.to_json_row() for e in es]
                       for et, es in entities.items()}, f, indent=4)


if __name__ == '__main__':
    configure_script_logging(log)
    main(sys.argv[1:])
