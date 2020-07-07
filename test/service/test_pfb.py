from typing import (
    cast,
)

import fastavro

from azul.plugins.metadata.hca import (
    FileTransformer,
)
from azul.service import (
    pfb,
)
from azul_test_case import (
    AzulUnitTestCase,
)


class TestPFB(AzulUnitTestCase):

    def test_pfb_schema(self):
        field_types = FileTransformer.field_types()
        tables_schema = list(pfb.tables_schema(field_types))
        final_schema = pfb.pfb_schema(tables_schema)
        fastavro.parse_schema(cast(dict, final_schema))

    def test_metadata_object(self):
        tables_schema = pfb.tables_schema(FileTransformer.field_types())
        metadata_entity = pfb.metadata_entity(FileTransformer.field_types())
        parsed_schema = fastavro.parse_schema(cast(dict, pfb.pfb_schema(tables_schema)))
        fastavro.validate(metadata_entity, parsed_schema)
