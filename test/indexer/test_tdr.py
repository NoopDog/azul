from functools import (
    lru_cache,
)
import json
from operator import (
    attrgetter,
    itemgetter,
)
from typing import (
    Mapping,
)
import unittest

from furl import (
    furl,
)
from more_itertools import (
    first,
    one,
)
from tinyquery import (
    tinyquery,
)

from azul import (
    RequirementError,
    cached_property,
    config,
)
from azul.bigquery import (
    BigQueryRow,
    BigQueryRows,
)
from azul.indexer import (
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
    JSONs,
)
from indexer import (
    CannedBundleTestCase,
)

snapshot_id = 'cafebabe-feed-4bad-dead-beaf8badf00d'
bundle_uuid = '1b6d8348-d6e9-406a-aa6a-7ee886e52bf9'


class TestTDRPlugin(CannedBundleTestCase):
    snapshot_source = TDRSource(project='test_project', name='snapshot', is_snapshot=True)
    dataset_source = TDRSource(project='test_project', name='dataset', is_snapshot=False)
    supp_files_source = TDRSource(project='test_project', name='snapshot_supp_files', is_snapshot=True)

    @cached_property
    def tinyquery(self) -> tinyquery.TinyQuery:
        return tinyquery.TinyQuery()

    def test_list_links_ids(self):
        for source in (self.snapshot_source, self.dataset_source):
            with self.subTest(str(source)):
                old_version = '2001-01-01T00:00:00.000000Z'
                current_version = '2001-01-01T00:00:00.000001Z'
                links_ids = ['42-abc', '42-def', '42-ghi', '86-xyz']
                versions = (current_version,) if source.is_snapshot else (current_version,
                                                                          old_version)
                self._load_mock_table(source=source,
                                      name='links',
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

    @lru_cache
    def _canned_bundle(self, source: TDRSource) -> TDRBundle:
        fqid = BundleFQID(bundle_uuid, '')
        metadata = self._load_canned_file(fqid, f'metadata.{source.name}.tdr')
        manifest = self._load_canned_file(fqid, f'manifest.{source.name}.tdr')
        return TDRBundle(source=source,
                         uuid=bundle_uuid,
                         version=one(e['version'] for e in manifest if e['name'] == 'links.json'),
                         manifest=manifest,
                         metadata_files=metadata)

    def _load_mock_tables(self,
                          source: TDRSource,
                          bundle_fqid: BundleFQID,
                          *,
                          tables: Mapping[str, JSONs] = None) -> None:
        if tables is None:
            tables = self._load_canned_file(bundle_fqid, f'bigquery.{source.name}.tdr')
        for table_name, table_rows in tables.items():
            self._load_mock_table(source, table_name, table_rows)

    def test_emulate_bundle(self):
        for source in (self.snapshot_source, self.dataset_source):
            with self.subTest(str(source)):
                self._test_bundle(source)

    def test_supplementary_file_links(self):
        with self.subTest('valid links'):
            self._test_bundle(self.supp_files_source)

        links_content_column = self.tinyquery.tables_by_name[
            f'{self.supp_files_source.bq_name}.links'
        ].columns['content'].values
        links_content = json.loads(one(links_content_column))
        link = first(link for link in links_content['links']
                     if link['link_type'] == 'supplementary_file_link')

        with self.subTest('invalid entity id'):
            link['entity']['entity_type'] = 'cell_suspension'
            links_content_column[0] = json.dumps(links_content)
            with self.assertRaises(RequirementError):
                self._test_bundle(self.supp_files_source, load_tables=False)

        with self.subTest('invalid entity type'):
            link['entity']['entity_type'] = 'project'
            link['entity']['entity_id'] += '_wrong'
            links_content_column[0] = json.dumps(links_content)
            with self.assertRaises(RequirementError):
                self._test_bundle(self.supp_files_source, load_tables=False)

    def _test_bundle(self,
                     source: TDRSource,
                     *,
                     load_tables: bool = True):
        test_bundle = self._canned_bundle(source)
        if load_tables:
            self._load_mock_tables(source, test_bundle.fquid)
        plugin = TestPlugin(source, self.tinyquery)
        emulated_bundle = plugin.emulate_bundle(test_bundle.fquid)
        self.assertEqual(test_bundle.fquid, emulated_bundle.fquid)
        self.assertEqual(test_bundle.metadata_files.keys(),
                         emulated_bundle.metadata_files.keys())

        def key_prefix(key):
            return key.rsplit('_', 1)[0]

        for key, value in test_bundle.metadata_files.items():
            # Ordering of entities is non-deterministic so "process_0.json" may
            # in fact be "process_1.json", etc
            self.assertEqual(1, len([k
                                     for k, v in emulated_bundle.metadata_files.items()
                                     if v == value and key_prefix(key) == key_prefix(k)]))
        for manifest in (test_bundle.manifest, emulated_bundle.manifest):
            manifest.sort(key=itemgetter('uuid'))

        for expected_entry, emulated_entry in zip(test_bundle.manifest,
                                                  emulated_bundle.manifest):
            self.assertEqual(expected_entry.keys(), emulated_entry.keys())
            for k, v1 in expected_entry.items():
                v2 = emulated_entry[k]
                if k == 'name' and expected_entry['indexed']:
                    self.assertEqual(key_prefix(v1), key_prefix(v2))
                else:
                    self.assertEqual(v1, v2)

    def _load_mock_table(self,
                         source: TDRSource,
                         name: str,
                         rows: JSONs) -> None:
        schema = self._bq_schema(rows[0])
        columns = {column['name'] for column in schema}
        # TinyQuery's errors are typically not helpful in debugging missing/
        # extra columns in the row JSON.
        for row in rows:
            row_columns = row.keys()
            assert row_columns == columns, row_columns
        self.tinyquery.load_table_from_newline_delimited_json(
            table_name=f'{source.bq_name}.{name}',
            schema=json.dumps(schema),
            table_lines=map(json.dumps, rows)
        )

    def _bq_schema(self, row: BigQueryRow) -> JSONs:
        return [
            dict(name=k,
                 type='TIMESTAMP' if k == 'version' else 'STRING',
                 mode='NULLABLE')
            for k, v in row.items()
        ]

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


if __name__ == '__main__':
    unittest.main()
