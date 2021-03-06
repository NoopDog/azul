from collections import (
    defaultdict,
)
import copy
from datetime import (
    datetime,
)
from functools import (
    lru_cache,
)
import json
from operator import (
    attrgetter,
    itemgetter,
)
from typing import (
    Dict,
    Mapping,
    Optional,
)
import unittest

from furl import (
    furl,
)
from more_itertools import (
    one,
)
from tinyquery import (
    tinyquery,
)

from azul import (
    RequirementError,
    cached_property,
    config,
    dss,
)
from azul.bigquery import (
    BigQueryRows,
)
from azul.indexer import (
    Bundle,
    BundleFQID,
)
from azul.plugins.repository import (
    tdr,
)
from azul.plugins.repository.tdr import (
    TDRBundle,
)
from azul.terra import (
    TDRSource,
)
from azul.types import (
    JSON,
    JSONs,
    MutableJSON,
    MutableJSONs,
)
from azul_test_case import (
    AzulUnitTestCase,
)

snapshot_id = 'cafebabe-feed-4bad-dead-beaf8badf00d'


class TestTDRPlugin(AzulUnitTestCase):

    @cached_property
    def tinyquery(self) -> tinyquery.TinyQuery:
        return tinyquery.TinyQuery()

    def test_list_links_ids(self):
        def test(source: TDRSource):
            old_version = '2001-01-01T00:00:00.000000Z'
            current_version = '2001-01-01T00:00:00.000001Z'
            links_ids = ['42-abc', '42-def', '42-ghi', '86-xyz']
            versions = (current_version,) if source.is_snapshot else (current_version, old_version)
            self._make_mock_entity_table(source=source,
                                         table_name='links',
                                         rows=[
                                             dict(links_id=links_id, version=version, content='{}')
                                             for version in versions
                                             for links_id in links_ids
                                         ])
            plugin = TestPlugin(source, self.tinyquery)
            bundle_ids = plugin.list_links_ids(prefix='42')
            bundle_ids.sort(key=attrgetter('uuid'))
            self.assertEqual(bundle_ids, [
                BundleFQID('42-abc', current_version),
                BundleFQID('42-def', current_version),
                BundleFQID('42-ghi', current_version)
            ])

        for is_snapshot in True, False:
            with self.subTest(is_snapshot=is_snapshot):
                test(self._test_source(is_snapshot=is_snapshot))

    def _test_source(self, *, is_snapshot: bool) -> TDRSource:
        return TDRSource(project='foo', name='bar', is_snapshot=is_snapshot)

    @lru_cache
    def _canned_bundle(self, source: TDRSource) -> TDRBundle:
        uuid = '1b6d8348-d6e9-406a-aa6a-7ee886e52bf9'
        version = '2001-01-01T00:00:00.000000Z'
        path = f'{config.project_root}/test/indexer/data/{uuid}'
        with open(path + '.metadata.json') as f:
            metadata = self.convert_metadata(json.load(f))
        with open(path + '.manifest.json') as f:
            manifest = self._convert_manifest(json.load(f), metadata, BundleFQID(uuid, version))
        return TDRBundle(source=source,
                         uuid=uuid,
                         version=version,
                         manifest=manifest,
                         metadata_files=metadata)

    def test_emulate_bundle(self):
        for is_snapshot in True, False:
            with self.subTest(is_snapshot=is_snapshot):
                self._test_bundle(self._test_source(is_snapshot=is_snapshot))

    def _test_bundle(self, source: TDRSource, test_bundle: Optional[Bundle] = None):
        if test_bundle is None:
            test_bundle = self._canned_bundle(source)
        self._make_mock_tdr_tables(source, test_bundle)
        plugin = TestPlugin(source, self.tinyquery)
        emulated_bundle = plugin.emulate_bundle(test_bundle.fquid)
        self.assertEqual(test_bundle.fquid, emulated_bundle.fquid)
        self.assertEqual(test_bundle.metadata_files.keys(), emulated_bundle.metadata_files.keys())

        def key_prefix(key):
            return key.rsplit('_', 1)[0]

        for key, value in test_bundle.metadata_files.items():
            # Ordering of entities is non-deterministic so "process_0.json" may in fact be "process_1.json", etc
            self.assertEqual(1, len([k
                                     for k, v in emulated_bundle.metadata_files.items()
                                     if v == value and key_prefix(key) == key_prefix(k)]))
        for manifest in (test_bundle.manifest, emulated_bundle.manifest):
            manifest.sort(key=itemgetter('uuid'))

        for expected_entry, emulated_entry in zip(test_bundle.manifest, emulated_bundle.manifest):
            self.assertEqual(expected_entry.keys(), emulated_entry.keys())
            for k, v1 in expected_entry.items():
                v2 = emulated_entry[k]
                if k == 'name' and expected_entry['indexed']:
                    self.assertEqual(key_prefix(v1), key_prefix(v2))
                elif k == 'drs_path' and not source.is_snapshot:
                    self.assertIsNone(v2)
                else:
                    self.assertEqual(v1, v2)

    def _make_mock_tdr_tables(self, source: TDRSource, bundle: Bundle):
        manifest_links = {entry['name']: entry for entry in bundle.manifest}

        def build_descriptor(document_name):
            entry = manifest_links[document_name]
            return dict(
                **tdr.Checksums.from_json(entry).to_json(),
                file_name=document_name,
                # file_id and uuid are NOT The same, but there's no appropriate value to use here.
                file_id=entry['uuid'],
                file_version=entry['version'],
                content_type=entry['content-type'].split(';', 1)[0],
                size=entry['size']
            )

        project_id = manifest_links['project_0.json']['uuid']
        self._make_mock_entity_table(source=source,
                                     table_name='links',
                                     additional_columns=dict(project_id=str),
                                     rows=[
                                         dict(links_id=bundle.uuid,
                                              project_id=project_id,
                                              version=bundle.version,
                                              content=json.dumps(bundle.metadata_files['links.json'])),
                                         dict(links_id=bundle.uuid,
                                              project_id='whatever',
                                              version=bundle.version.replace('0', '1'),
                                              content='wrong version'),
                                         dict(links_id=bundle.uuid + 'bad',
                                              project_id='whatever',
                                              version=bundle.version,
                                              content='wrong links_id')
                                     ])
        entities = defaultdict(list)
        for document_name, entity in bundle.metadata_files.items():
            if document_name != 'links.json':
                entities[self._concrete_type(entity)].append((entity, manifest_links[document_name]))

        for entity_type, typed_entities in entities.items():
            rows = []
            for metadata_file, manifest_entry in typed_entities:
                uuid = manifest_entry['uuid']
                version = manifest_entry['version']
                versions = (version,) if source.is_snapshot else (version,
                                                                  version.replace('2019', '2009'),
                                                                  version.replace('2019', '1999'))
                uuids = (uuid, 'wrong', 'wronger')

                if entity_type.endswith('_file'):
                    descriptor = build_descriptor(metadata_file['file_core']['file_name'])
                    data_columns = {
                        'descriptor': json.dumps(descriptor),
                        'file_id': self._drs_file_id(descriptor['file_id']) if source.is_snapshot else None
                    }
                else:
                    data_columns = {}

                for uuid in uuids:
                    for version in versions:
                        rows.append({
                            f'{entity_type}_id': uuid,
                            'version': version,
                            'content': json.dumps(metadata_file),
                            **data_columns,
                        })
            self._make_mock_entity_table(source=source,
                                         table_name=entity_type,
                                         rows=rows)

    def test_supplementary_file_links(self):
        fake_checksums = tdr.Checksums(crc32c='a', sha256='b')
        supp_file_version = '2001-01-01T00:00:00.000000Z'
        supp_files = {
            file_id: (
                {
                    "describedBy": ".../supplementary_file",
                    "schema_type": "file",
                    "provenance": {
                        "document_id": file_id
                    },
                    "file_core": {
                        "file_name": f"{file_id}_name"
                    }
                }, {
                    "file_name": f"{file_id}_name",
                    "file_id": file_id * 2,
                    "file_version": supp_file_version,
                    "content_type": "whatever format these are in",
                    "size": 1024,
                    **fake_checksums.to_json()
                }
            )
            for file_id in ['123', '456', '789']
        }

        source = self._test_source(is_snapshot=False)
        self._make_mock_entity_table(source=source,
                                     table_name='supplementary_file',
                                     rows=[
                                         dict(supplementary_file_id=uuid,
                                              version=supp_file_version,
                                              content=json.dumps(content),
                                              descriptor=json.dumps(descriptor),
                                              file_id=self._drs_file_id(descriptor['file_id']))
                                         for uuid, (content, descriptor) in supp_files.items()
                                     ])

        test_bundle = copy.deepcopy(self._canned_bundle(source))
        project = test_bundle.metadata_files['project_0.json']['provenance']['document_id']
        # Add new entries to manifest
        for i, (uuid, (content, descriptor)) in enumerate(supp_files.items()):
            name = f'supplementary_file_{i}.json'
            test_bundle.metadata_files[name] = content
            test_bundle.manifest.append({
                'name': name,
                'uuid': uuid,
                'version': supp_file_version,
                'size': len(json.dumps(content).encode('UTF-8')),
                'indexed': True,
                'content-type': 'application/json; dcp-type="metadata/file"',
                'crc32c': '',
                'sha256': ''
            })
            test_bundle.manifest.append({
                'name': descriptor['file_name'],
                'uuid': descriptor['file_id'],
                'version': supp_file_version,
                'size': descriptor['size'],
                'indexed': False,
                'content-type': f"{descriptor['content_type']}; dcp-type=data",
                'drs_path': f"v1_{snapshot_id}_{descriptor['file_id']}",
                **fake_checksums.to_json()
            })
        # Link them
        new_link = {
            "link_type": "supplementary_file_link",
            "entity": {
                "entity_type": "project",
                "entity_id": project
            },
            "files": [
                {
                    "file_type": "supplementary_file",
                    "file_id": k
                }
                for k in supp_files.keys()
            ]
        }
        test_bundle.metadata_files['links.json']['links'].append(new_link)
        # Update size in manifest to include new links
        one(
            e for e in test_bundle.manifest if e['name'] == 'links.json'
        )['size'] = len(json.dumps(test_bundle.metadata_files['links.json']))

        with self.subTest('valid links'):
            self._test_bundle(source, test_bundle)

        with self.subTest('invalid entity id'):
            new_link['entity']['entity_type'] = 'cell_suspension'
            with self.assertRaises(RequirementError):
                self._test_bundle(source, test_bundle)

        with self.subTest('invalid entity type'):
            new_link['entity']['entity_type'] = 'project'
            new_link['entity']['entity_id'] = project + 'bad'
            with self.assertRaises(RequirementError):
                self._test_bundle(source, test_bundle)

    def convert_metadata(self, metadata: MutableJSON) -> MutableJSON:
        """
        Convert one of the testing bundles from DCP/1 to conform to the TDR spec
        """

        metadata = copy.deepcopy(metadata)
        metadata['links.json']['schema_type'] = 'links'  # DCP/1 uses 'link_bundle'

        def find_concrete_type(document_id):
            return self._concrete_type(one(v
                                           for k, v in metadata.items()
                                           if k != 'links.json' and v['provenance']['document_id'] == document_id))

        for link in metadata['links.json']['links']:
            process_id = link.pop('process')
            link['process_id'] = process_id
            link['process_type'] = find_concrete_type(process_id)
            link['link_type'] = 'process_link'  # No supplementary files in DCP/1 bundles
            for component in ('input', 'output'):  # Protocols already in desired format
                link.pop(f'{component}_type')
                component_list = link[f'{component}s']
                component_list[:] = [
                    {
                        f'{component}_id': component_id,
                        f'{component}_type': find_concrete_type(component_id)
                    }
                    for component_id in component_list
                ]
        return metadata

    def _convert_manifest(self,
                          manifest: MutableJSONs,
                          metadata: JSON,
                          bundle_fqid: BundleFQID
                          ) -> MutableJSONs:
        """
        Remove and alter entries in DCP/1 bundle to match expected format for TDR
        """
        manifest = [entry.copy() for entry in manifest]
        for entry in manifest:
            entry['version'] = datetime.strptime(
                entry['version'],
                dss.version_format
            ).strftime(
                tdr.Plugin.timestamp_format
            )
            if entry['indexed']:
                entry['size'] = len(json.dumps(metadata[entry['name']]).encode('UTF-8'))
                del entry['sha1']
                del entry['s3_etag']
                entry['crc32c'] = ''
                entry['sha256'] = ''
            else:
                # Again, usage of uuid is not the correct value here.
                entry['drs_path'] = f"v1_{snapshot_id}_{entry['uuid']}"

            if entry['name'] == 'links.json':
                # links.json has no FQID of its own in TDR since its FQID is used for the entire bundle
                entry['version'] = bundle_fqid.version
                entry['uuid'] = bundle_fqid.uuid
        return manifest

    def _concrete_type(self, entity_json):
        return entity_json['describedBy'].rsplit('/', 1)[1]

    def _make_mock_entity_table(self,
                                *,
                                source: TDRSource,
                                table_name: str,
                                rows: JSONs = (),
                                additional_columns: Optional[Mapping[str, type]] = None):
        columns: Dict[str, type] = {
            f'{table_name}_id': str,
            'version': datetime,
            'content': str
        }
        if additional_columns is not None:
            columns.update(additional_columns)
        if table_name.endswith('_file'):
            columns['descriptor'] = str
            columns['file_id'] = str
        self._create_table(dataset_name=source.bq_name,
                           table_name=table_name,
                           schema=self._bq_schema(columns),
                           rows=rows)

    _bq_types: Mapping[type, str] = {
        bool: 'BOOL',
        datetime: 'TIMESTAMP',
        bytes: 'BYTES',
        float: 'FLOAT',
        int: 'INTEGER',
        str: 'STRING'
    }

    def _bq_schema(self, columns: Mapping[str, type]) -> JSONs:
        return [
            dict(name=k,
                 type=self._bq_types[v] if isinstance(v, type) else v,
                 mode='NULLABLE')
            for k, v in columns.items()
        ]

    def _create_table(self, dataset_name: str, table_name: str, schema: JSONs, rows: JSONs) -> None:
        # TinyQuery's errors are typically not helpful in debugging missing/extra columns in the row JSON.
        columns = sorted([column['name'] for column in schema])
        for row in rows:
            row_columns = sorted(row.keys())
            assert row_columns == columns, row_columns
        self.tinyquery.load_table_from_newline_delimited_json(table_name=f'{dataset_name}.{table_name}',
                                                              schema=json.dumps(schema),
                                                              table_lines=map(json.dumps, rows))

    def _drs_file_id(self, file_id):
        netloc = furl(config.tdr_service_url).netloc
        return f'drs://{netloc}/v1_{snapshot_id}_{file_id}'


class TestPlugin(tdr.Plugin):

    def __init__(self, source: TDRSource, tinyquery: tinyquery.TinyQuery) -> None:
        super().__init__(source)
        self._tinyquery = tinyquery

    def _run_sql(self, query: str) -> BigQueryRows:
        columns = self._tinyquery.evaluate_query(query).columns
        num_rows = one(set(map(lambda c: len(c.values), columns.values())))
        for i in range(num_rows):
            yield {k[1]: v.values[i] for k, v in columns.items()}

    def _full_table_name(self, table_name: str) -> str:
        return self._source.bq_name + '.' + table_name


if __name__ == '__main__':
    unittest.main()
