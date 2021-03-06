import logging

from azul import (
    config,
)
from azul.logging import (
    configure_script_logging,
)
from azul.terra import (
    TDRClient,
    TDRSource,
)

log = logging.getLogger(__name__)


def main():
    configure_script_logging(log)
    tdr = TDRClient()
    tdr.register_with_sam()

    tdr_catalogs = (
        catalog
        for catalog, plugins in config.catalogs.items()
        if plugins['repository'] == 'tdr'
    )
    for source in set(map(config.tdr_source, tdr_catalogs)):
        source = TDRSource.parse(source)
        tdr.check_api_access(source)
        tdr.check_bigquery_access(source)


if __name__ == '__main__':
    main()
