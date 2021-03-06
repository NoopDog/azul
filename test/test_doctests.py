import doctest
import unittest

import azul
import azul.azulclient
import azul.collections
import azul.doctests
import azul.dss
import azul.exceptions
import azul.files
import azul.indexer.aggregate
import azul.json
import azul.json_freeze
from azul.logging import (
    configure_test_logging,
)
from azul.modules import (
    load_app_module,
    load_module,
)
import azul.objects
import azul.openapi
import azul.openapi.params
import azul.openapi.responses
import azul.openapi.schema
import azul.plugins.metadata.hca.contributor_matrices
import azul.plugins.metadata.hca.full_metadata
import azul.plugins.metadata.hca.transform
import azul.plugins.repository.tdr
import azul.service.drs_controller
import azul.service.manifest_service
import azul.strings
import azul.terra
import azul.threads
import azul.time
import azul.types
import azul.uuids
import azul.vendored.frozendict
import retorts


# noinspection PyPep8Naming
def setupModule():
    configure_test_logging()


def load_tests(_loader, tests, _ignore):
    root = azul.config.project_root
    for module in [
        azul,
        azul.azulclient,
        azul.collections,
        azul.doctests,
        azul.dss,
        azul.exceptions,
        azul.files,
        azul.indexer.aggregate,
        azul.json,
        azul.json_freeze,
        azul.objects,
        azul.openapi,
        azul.openapi.params,
        azul.openapi.responses,
        azul.openapi.schema,
        azul.plugins.metadata.hca.contributor_matrices,
        azul.plugins.metadata.hca.full_metadata,
        azul.plugins.repository.tdr,
        azul.plugins.metadata.hca.transform,
        azul.service.drs_controller,
        azul.service.manifest_service,
        azul.strings,
        azul.terra,
        azul.threads,
        azul.time,
        azul.types,
        azul.uuids,
        azul.vendored.frozendict,
        retorts,
        load_app_module('service'),
        load_module(root + '/scripts/envhook.py', 'envhook'),
        load_module(root + '/scripts/export_environment.py', 'export_environment'),
        load_module(root + '/scripts/check_branch.py', 'check_branch'),
        load_module(root + '/scripts/velocity.py', 'velocity'),
        load_module(root + '/.flake8/azul_flake8.py', 'azul_flake8')
    ]:
        suite = doctest.DocTestSuite(module)
        assert suite.countTestCases() > 0, module
        tests.addTests(suite)
    return tests


if __name__ == '__main__':
    unittest.main()
