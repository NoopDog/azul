import json
import os
from random import (
    shuffle,
)
import sys
from tempfile import (
    TemporaryDirectory,
)
import unittest
from unittest import (
    mock,
)

from furl import (
    furl,
)
from more_itertools import (
    one,
)
import requests
from requests import (
    Response,
)

import azul.changelog
from azul.logging import (
    configure_test_logging,
)
from azul.plugins import (
    MetadataPlugin,
)
from service import (
    WebServiceTestCase,
)


# noinspection PyPep8Naming
def setUpModule():
    configure_test_logging()


class RequestParameterValidationTest(WebServiceTestCase):

    def unknown_filter_facet_test(self, response: Response, index_position):
        self.assertIn('filters',
                      response.json()['invalid_parameters'][index_position]['name'])
        self.assertIn("Unknown facet 'bad-facet'",
                      response.json()['invalid_parameters'][index_position]['message'])

    def invalid_sort_test(self, response: Response, index_position):
        self.assertIn('sort',
                      response.json()['invalid_parameters'][index_position]['name'])
        self.assertIn('Invalid parameter',
                      response.json()['invalid_parameters'][index_position]['message'])

    def test_version(self):
        commit = 'a9eb85ea214a6cfa6882f4be041d5cce7bee3e45'
        with TemporaryDirectory() as tmpdir:
            azul.changelog.write_changes(tmpdir)
            with mock.patch('sys.path', new=sys.path + [tmpdir]):
                for dirty in True, False:
                    with self.subTest(is_repo_dirty=dirty):
                        with mock.patch.dict(os.environ, azul_git_commit=commit, azul_git_dirty=str(dirty)):
                            url = self.base_url + "/version"
                            response = requests.get(url)
                            response.raise_for_status()
                            expected_json = {
                                'commit': commit,
                                'dirty': dirty
                            }
                            self.assertEqual(response.json()['git'], expected_json)

    def test_bad_single_filter_facet_of_sample(self):
        url = self.base_url + '/index/samples'
        params = {
            'catalog': self.catalog,
            'size': 1,
            'filters': json.dumps({'bad-facet': {'is': ['fake-val']}}),
        }
        response = requests.get(url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.assertEqual(1, len(response.json()['invalid_parameters']))
        self.unknown_filter_facet_test(response, 0)

    def test_bad_multiple_filter_facet_of_sample(self):
        url = self.base_url + '/index/samples'
        params = {
            'catalog': self.catalog,
            'size': 1,
            'filters': json.dumps({'bad-facet': {'is': ['fake-val']}, 'bad-facet2': {'is': ['fake-val2']}}),
        }
        response = requests.get(url, params=params)
        self.assertEqual(1, len(response.json()['invalid_parameters']))
        self.unknown_filter_facet_test(response, 0)

    def test_mixed_multiple_filter_facet_of_sample(self):
        url = self.base_url + '/index/samples'
        params = {
            'catalog': self.catalog,
            'size': 1,
            'filters': json.dumps({'organPart': {'is': ['fake-val']}, 'bad-facet': {'is': ['fake-val']}}),
        }
        response = requests.get(url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.assertEqual(1, len(response.json()['invalid_parameters']))
        self.unknown_filter_facet_test(response, 0)

    def test_bad_sort_facet_of_sample(self):
        url = self.base_url + '/index/samples'
        params = {
            'size': 1,
            'filters': json.dumps({}),
            'sort': 'bad-facet',
            'order': 'asc',
        }
        response = requests.get(url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.assertEqual(1, len(response.json()['invalid_parameters']))
        self.invalid_sort_test(response, 0)

    def test_bad_sort_facet_and_filter_facet_of_sample(self):
        url = self.base_url + '/index/samples'

        params = {
            'size': 15,
            'filters': json.dumps({'bad-facet': {'is': ['fake-val']}}),
            'sort': 'bad-facet',
            'order': 'asc',
        }
        response = requests.get(url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.assertEqual(2, len(response.json()['invalid_parameters']))
        self.unknown_filter_facet_test(response, 0)
        self.invalid_sort_test(response, 1)

    def test_valid_sort_facet_but_bad_filter_facet_of_sample(self):
        url = self.base_url + '/index/samples'
        params = {
            'catalog': self.catalog,
            'size': 15,
            'filters': json.dumps({'bad-facet': {'is': ['fake-val']}}),
            'sort': 'organPart',
            'order': 'asc',
        }
        response = requests.get(url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.assertEqual(1, len(response.json()['invalid_parameters']))
        self.unknown_filter_facet_test(response, 0)

    def test_bad_sort_facet_but_valid_filter_facet_of_sample(self):
        url = self.base_url + '/index/samples'
        params = {
            'size': 15,
            'filters': json.dumps({'organPart': {'is': ['fake-val2']}}),
            'sort': 'bad-facet',
            'order': 'asc',
        }
        response = requests.get(url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.assertEqual(1, len(response.json()['invalid_parameters']))
        self.invalid_sort_test(response, 0)

    def test_bad_single_filter_facet_of_file(self):
        url = self.base_url + '/index/files'
        params = {
            'catalog': self.catalog,
            'size': 1,
            'filters': json.dumps({'bad-facet': {'is': ['fake-val2']}}),
        }
        response = requests.get(url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.assertEqual(1, len(response.json()['invalid_parameters']))
        self.unknown_filter_facet_test(response, 0)

    def test_bad_multiple_filter_facet_of_file(self):
        url = self.base_url + '/index/files'
        params = {
            'catalog': self.catalog,
            'size': 1,
            'filters': json.dumps({'bad-facet': {'is': ['fake-val']}, 'bad-facet2': {'is': ['fake-val2']}}),
        }
        response = requests.get(url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.assertEqual(1, len(response.json()['invalid_parameters']))
        self.unknown_filter_facet_test(response, 0)

    def test_mixed_multiple_filter_facet_of_file(self):
        url = self.base_url + '/index/files'
        params = {
            'catalog': self.catalog,
            'size': 1,
            'filters': json.dumps({'organPart': {'is': ['fake-val']}, 'bad-facet': {'is': ['fake-val']}}),
        }
        response = requests.get(url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.assertEqual(1, len(response.json()['invalid_parameters']))
        self.unknown_filter_facet_test(response, 0)

    def test_bad_sort_facet_of_file(self):
        url = self.base_url + '/index/files'
        params = {
            'size': 15,
            'sort': 'bad-facet',
            'order': 'asc',
            'filters': json.dumps({}),
        }
        response = requests.get(url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.assertEqual(1, len(response.json()['invalid_parameters']))
        self.invalid_sort_test(response, 0)

    def test_bad_sort_facet_and_filter_facet_of_file(self):
        url = self.base_url + '/index/files'
        params = {
            'catalog': self.catalog,
            'size': 15,
            'filters': json.dumps({'bad-facet': {'is': ['fake-val2']}}),
        }
        response = requests.get(url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.unknown_filter_facet_test(response, 0)

    def test_bad_sort_facet_but_valid_filter_facet_of_file(self):
        url = self.base_url + '/index/files'
        params = {
            'size': 15,
            'sort': 'bad-facet',
            'order': 'asc',
            'filters': json.dumps({'organ': {'is': ['fake-val2']}}),
        }
        response = requests.get(url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.invalid_sort_test(response, 0)

    def test_valid_sort_facet_but_bad_filter_facet_of_file(self):

        url = self.base_url + '/index/files'
        params = {
            'catalog': self.catalog,
            'size': 15,
            'sort': 'organPart',
            'order': 'asc',
            'filters': json.dumps({'bad-facet': {'is': ['fake-val2']}}),
        }
        response = requests.get(url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.unknown_filter_facet_test(response, 0)

    def test_single_entity_error_responses(self):
        entity_types = ['files', 'projects']
        for uuid, expected_error_code in [('2b7959bb-acd1-4aa3-9557-345f9b3c6327', 404),
                                          ('-0c5ac7c0-817e-40d4-b1b1-34c3d5cfecdb-', 400),
                                          ('FOO', 400)]:
            for entity_type in entity_types:
                with self.subTest(entity_name=entity_type, error_code=expected_error_code, uuid=uuid):
                    url = self.base_url + f'/index/{entity_type}/{uuid}'
                    response = requests.get(url)
                    self.assertEqual(expected_error_code, response.status_code)

    def test_file_order(self):
        url = self.base_url + '/index/files/order'
        response = requests.get(url)
        self.assertEqual(200, response.status_code, response.json())
        actual_field_order = response.json()['order']
        plugin = MetadataPlugin.load(self.catalog).create()
        expected_field_order = plugin.service_config().order_config
        self.assertEqual(expected_field_order, actual_field_order)

    def test_bad_query_params(self):
        for entity_type in ('files', 'bundles', 'samples'):
            url = self.base_url + f'/index/{entity_type}'
            with self.subTest(entity_type=entity_type):
                with self.subTest(test='extra parameter'):
                    params = dict(catalog=self.catalog,
                                  some_nonexistent_filter=1)
                    response = requests.get(url, params=params)
                    status_code = 400
                    self.assertEqual(status_code, response.status_code, response.content)
                    self.assertEqual(1, len(response.json()['extra_parameters']))
                    self.assertIn('some_nonexistent_filter', response.json()['extra_parameters'])
                with self.subTest(test='malformed parameter'):
                    params = dict(catalog=self.catalog,
                                  size='foo')
                    response = requests.get(url, params=params)
                    status_code = 400
                    self.assertEqual(status_code, response.status_code, response.content)
                    self.assertEqual('size', one(response.json()['invalid_parameters'])['name'])
                    self.assertIn("Invalid parameter",
                                  one(response.json()['invalid_parameters'])['message'])
                with self.subTest(test='malformed filter parameter'):
                    params = dict(catalog=self.catalog,
                                  filters='{"}')
                    response = requests.get(url, params=params)
                    status_code = 400
                    self.assertEqual(status_code, response.status_code, response.content)
                    self.assertEqual('filters', one(response.json()['invalid_parameters'])['name'])
                    self.assertIn('Invalid JSON',
                                  one(response.json()['invalid_parameters'])['message'])
        if '/integrations' in self.app_module.app.specs['paths'].keys():
            self.fail(msg="Remove conditional check to allow parameters for '/integrations' to be tested")
            # TODO: place this subtest back when the `/integrations` endpoint is spec'd out.
            # see https://github.com/DataBiosphere/azul/issues/1984
            # noinspection PyUnreachableCode
            with self.subTest(test='missing required parameter'):
                url = self.base_url + '/integrations'
                params = {}
                response = requests.get(url, params=params)
                status_code = 400
                self.assertEqual(status_code, response.status_code, response.content)
                self.assertIn('Missing required parameters in request',
                              response.json()['title'])

    def test_bad_filter_relation(self):
        url = self.base_url + '/index/files'
        params = {
            'size': 15,
            'sort': 'organPart',
            'order': 'asc',
            'filters': json.dumps({'organPart': {'bad': ['fake-val2']}}),
        }
        response = requests.get(url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.assertIn(
            "Unknown relation in the 'filters' parameter entry for",
            one(response.json()['invalid_parameters'])['message'])

    def test_single_facet_multiple_relations(self):
        url = furl(url=self.base_url, path='/index/files').url
        params = {
            'filters': json.dumps({'organPart': {'bad': ['fake-val2'], 'foo': ['bar']}})
        }
        response = requests.get(url=url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.assertIn("'organPart' may only specify a single relation",
                      one(response.json()['invalid_parameters'])['message'])

    def test_multiple_facets_multiple_relations(self):
        url = furl(url=self.base_url, path='/index/files').url
        filters = {
            'organPart': {
                'is': ['foo'],
            },
            'entryId': {
                'is': ['bar'],
                'within': ['baz']
            }
        }
        response = requests.get(url=url, params={'filters': json.dumps(filters)})
        self.assertEqual(400, response.status_code, response.json())
        self.assertIn("'entryId' may only specify a single relation",
                      one(response.json()['invalid_parameters'])['message'])

    def test_bad_relation_type(self):
        url = furl(url=self.base_url, path='/index/files').url
        filters = {'organPart': "'is': ['foo']'"}
        response = requests.get(url=url, params={'filters': json.dumps(filters)})
        self.assertEqual(400, response.status_code, response.json())
        self.assertIn("'organPart' must be a JSON object",
                      one(response.json()['invalid_parameters'])['message'])

    def test_bad_nested_relation_value(self):
        path = '/index/files'
        url = furl(url=self.base_url, path=path)
        facet = 'organPart'
        for relation in ['contains', 'within', 'intersects']:
            invalid_filter_item_type = {facet: {relation: [[23, 33], 'bar']}}
            invalid_filter_item_count = {facet: {relation: [[23, 33, 70]]}}
            for invalid_filter in (invalid_filter_item_type, invalid_filter_item_count):
                with self.subTest(relation=relation, invalid_filter=invalid_filter):
                    params = {'filters': json.dumps(invalid_filter)}
                    response = requests.get(url.add(query_params=params).url)
                    self.assertEqual(400, response.status_code, response.json())
                    message = (f"The value of the {relation!r} relation in the 'filters' parameter "
                               f"entry for {facet!r} is invalid")
                    self.maxDiff = None
                    self.assertIn(message, one(response.json()['invalid_parameters'])['message'])

    def test_invalid_uuid(self):
        url = furl(url=self.base_url, path='/repository/files/foo').url
        response = requests.get(url, params={'replica': 'aws'})
        self.assertEqual(400, response.status_code, response.json())
        self.assertIn("'foo' is not a valid UUID.",
                      one(response.json()['invalid_parameters'])['message'])

    def test_extra_params(self):
        url = furl(url=self.base_url, path='/index/files/').url
        response = requests.get(url, params={'foo': 'bar'})
        self.assertEqual(400, response.status_code, response.json())
        self.assertEqual(1, len(response.json()['extra_parameters']))
        self.assertIn('foo', response.json()['extra_parameters'])

    def test_bad_catalog_param(self):
        url = furl(url=self.base_url, path='/index/files').url
        for catalog, error in [
            ('foo', 'Invalid parameter'),
            ('foo bar', 'Invalid characters within parameter')
        ]:
            response = requests.get(url=url, params={'catalog': catalog})
            self.assertEqual(400, response.status_code, response.json())
            self.assertIn(error, one(response.json()['invalid_parameters'])['message'])

    def test_deterministic_response(self):
        url = furl(url=self.base_url, path='/index/files').url
        shuffle_parameters = [
            ('filters', '{"}'),
            ('catalog', 'foo'),
            ('sort', 'bar'),
            ('order', 'asc')]
        shuffle(shuffle_parameters)
        params = {parameter[0]: parameter[1] for parameter in shuffle_parameters}
        response = requests.get(url=url, params=params)
        self.assertEqual(400, response.status_code, response.json())
        self.assertEqual(3, len(response.json()['invalid_parameters']))
        self.assertEqual('catalog', response.json()['invalid_parameters'][0]['name'])
        self.assertEqual('filters', response.json()['invalid_parameters'][1]['name'])
        self.assertEqual('sort', response.json()['invalid_parameters'][2]['name'])

    @unittest.skip('https://github.com/DataBiosphere/azul/issues/2465')
    def test_missing_uuid(self):
        url = furl(url=self.base_url, path='/fetch/repository/files/').url
        response = requests.get(url, params={'replica': 'aws'})
        self.assertEqual(400, response.status_code)

    def test_default_for_missing_params(self):
        path = '/test/mock/endpoints'
        method_spec = {
            'parameters': [
                {
                    'in': 'query',
                    'schema': {
                        'type': 'string',
                        'pattern': '^([a-z0-9]{1,64})$',
                        'enum': [self.catalog],
                        **({} if default is None else {'default': default})
                    },
                    'required': required,
                    'name': f'required-{required}-default-{default}'
                } for required in (True, False) for default in (self.catalog, None)
            ],
            'responses': {
                '200': {
                    'description': 'OK'
                }
            }
        }

        @self.app_module.app.route(path,
                                   validate=True,
                                   path_spec=None,
                                   method_spec=method_spec,
                                   methods=['GET'])
        def test_method():
            return dict(self.app_module.app.current_request.query_params)

        url = furl(url=self.base_url, path=path).url
        response = requests.get(url=url)
        self.assertEqual(400, response.status_code, response.json())
        self.assertEqual('required-True-default-None',
                         one(response.json()['missing_parameters'])['name'])
        response = requests.get(url=url,
                                params={'required-True-default-None': self.catalog})
        response.raise_for_status()
        self.assertEqual({
            'required-True-default-None': 'test',
            'required-True-default-test': 'test',
            'required-False-default-test': 'test'
        }, response.json())
