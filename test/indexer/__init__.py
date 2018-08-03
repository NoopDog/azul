from typing import Mapping, Any
from uuid import uuid4

from azul import config
from azul.project.hca.config import IndexProperties
from azul.project.hca.indexer import Indexer
from shared import AzulTestCase
import os


class IndexerTestCase(AzulTestCase):

    index_properties = None
    hca_indexer = None

    _old_dss_endpoint = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._old_dss_endpoint = os.environ.get('AZUL_DSS_ENDPOINT')
        os.environ['AZUL_DSS_ENDPOINT'] = "https://dss.data.humancellatlas.org/v1"
        cls.index_properties = IndexProperties(dss_url=config.dss_endpoint,
                                               es_endpoint=config.es_endpoint)
        cls.hca_indexer = Indexer(cls.index_properties)

    @classmethod
    def tearDownClass(cls):
        if cls._old_dss_endpoint is None:
            del os.environ['AZUL_DSS_ENDPOINT']
        else:
            os.environ['AZUL_DSS_ENDPOINT'] = cls._old_dss_endpoint
        super().tearDownClass()

    def _make_fake_notification(self, uuid: str, version: str) -> Mapping[str, Any]:
        return {
            "query": {
                "match_all": {}
            },
            "subscription_id": str(uuid4()),
            "transaction_id": str(uuid4()),
            "match": {
                "bundle_uuid": uuid,
                "bundle_version": version
            }
        }
