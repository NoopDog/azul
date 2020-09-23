from itertools import (
    chain,
)
import json
import logging
from typing import (
    Any,
    List,
    Mapping,
    Optional,
)
from urllib.parse import (
    urlencode,
)

import elasticsearch
from elasticsearch import (
    Elasticsearch,
)
from elasticsearch_dsl import (
    A,
    Q,
    Search,
)
from elasticsearch_dsl.aggs import (
    Agg,
    Terms,
)
from elasticsearch_dsl.response import (
    AggResponse,
    Response,
)
from elasticsearch_dsl.response.aggs import (
    Bucket,
    BucketData,
    FieldBucket,
    FieldBucketData,
)
from more_itertools import (
    one,
)

from azul import (
    CatalogName,
    cached_property,
    config,
)
from azul.es import (
    ESClientFactory,
)
from azul.indexer.document_service import (
    DocumentService,
)
from azul.plugins import (
    ServiceConfig,
)
from azul.service import (
    AbstractService,
    BadArgumentException,
    Filters,
    MutableFilters,
)
from azul.service.hca_response_v5 import (
    AutoCompleteResponse,
    FileSearchResponse,
    KeywordSearchResponse,
    SummaryResponse,
)
from azul.service.utilities import (
    json_pp,
)
from azul.types import (
    JSON,
)

logger = logging.getLogger(__name__)


class IndexNotFoundError(Exception):

    def __init__(self, missing_index: str):
        super().__init__(f'Index `{missing_index}` was not found')


SourceFilters = List[str]


class ElasticsearchService(DocumentService, AbstractService):

    @cached_property
    def es_client(self) -> Elasticsearch:
        return ESClientFactory.get()

    def __init__(self, service_config: Optional[ServiceConfig] = None):
        self._service_config = service_config

    def service_config(self, catalog: CatalogName):
        return self._service_config or self.metadata_plugin(catalog).service_config()

    def _translate_filters(self,
                           catalog: CatalogName,
                           filters: Filters,
                           field_mapping: JSON
                           ) -> MutableFilters:
        """
        Function for translating the filters

        :param catalog: the name of the catalog to translate filters for.
                        Different catalogs may use different field types,
                        resulting in differently translated filter.

        :param filters: Raw filters from the filters param. That is, in 'browserForm'

        :param field_mapping: Mapping config json with '{'browserKey': 'es_key'}' format

        :return: Returns translated filters with 'es_keys'
        """
        translated_filters = {}
        for key, value in filters.items():
            try:
                # Replace the key in the filter with the name within ElasticSearch
                key = field_mapping[key]
            except KeyError:
                pass  # FIXME: Isn't this an error (https://github.com/DataBiosphere/azul/issues/1205)?
            # Replace the value in the filter with the value translated for None values
            assert isinstance(value, dict)
            assert isinstance(one(value.values()), list)
            field_type = self.field_type(catalog, tuple(key.split('.')))
            value = {
                key: [field_type.to_index(v) for v in val]
                for key, val in value.items()
            }
            translated_filters[key] = value
        return translated_filters

    def _create_query(self, catalog: CatalogName, filters):
        """
        Creates a query object based on the filters argument

        :param catalog: The catalog against which to create the query for.

        :param filters: filter parameter from the /files endpoint with
        translated (es_keys) keys
        :return: Returns Query object with appropriate filters
        """
        filter_list = []
        for facet, values in filters.items():
            relation, value = one(values.items())
            if relation == 'is':
                query = Q('terms', **{facet + '.keyword': value})
                field_type = self.field_type(catalog, tuple(facet.split('.')))
                translated_none = field_type.to_index(None)
                if translated_none in value:
                    # Note that at this point None values in filters have already
                    # been translated eg. {'is': ['~null']} and if the filter has a
                    # None our query needs to find fields with None values as well
                    # as absent fields
                    absent_query = Q('bool', must_not=[Q('exists', field=facet)])
                    query = Q('bool', should=[query, absent_query])
                filter_list.append(query)
            elif relation in ('contains', 'within', 'intersects'):
                for min_value, max_value in value:
                    range_value = {
                        'gte': min_value,
                        'lte': max_value,
                        'relation': relation
                    }
                    filter_list.append(Q('range', **{facet: range_value}))
            else:
                assert False
        # Each iteration will AND the contents of the list
        query_list = [Q('constant_score', filter=f) for f in filter_list]

        return Q('bool', must=query_list)

    def _create_aggregate(self, catalog: CatalogName, filters: MutableFilters, facet_config, agg):
        """
        Creates the aggregation to be used in ElasticSearch

        :param catalog: The name of the catalog to create the aggregations for

        :param filters: Translated filters from 'files/' endpoint call

        :param facet_config: Configuration for the facets (i.e. facets on which
               to construct the aggregate) in '{browser:es_key}' form

        :param agg: Current aggregate where this aggregation is occurring.
                    Syntax in browser form

        :return: returns an Aggregate object to be used in a Search query
        """
        # Pop filter of current Aggregate
        excluded_filter = filters.pop(facet_config[agg], None)
        # Create the appropriate filters
        filter_query = self._create_query(catalog, filters)
        # Create the filter aggregate
        aggregate = A('filter', filter_query)
        # Make an inner aggregate that will contain the terms in question
        _field = f'{facet_config[agg]}.keyword'
        service_config = self.service_config(catalog)
        if agg == 'project':
            _sub_field = service_config.translation['projectId'] + '.keyword'
            aggregate.bucket('myTerms', 'terms', field=_field, size=config.terms_aggregation_size).bucket(
                'myProjectIds', 'terms', field=_sub_field, size=config.terms_aggregation_size)
        else:
            aggregate.bucket('myTerms', 'terms', field=_field, size=config.terms_aggregation_size)
        aggregate.bucket('untagged', 'missing', field=_field)
        if agg == 'fileFormat':
            # FIXME: Use of shadow field is brittle
            #        https://github.com/DataBiosphere/azul/issues/2289
            file_size_field = service_config.translation['fileSize'] + '_'
            aggregate.aggs['myTerms'].metric('size_by_type', 'sum', field=file_size_field)
            aggregate.aggs['untagged'].metric('size_by_type', 'sum', field=file_size_field)
        # If the aggregate in question didn't have any filter on the API
        #  call, skip it. Otherwise insert the popped
        # value back in
        if excluded_filter is not None:
            filters[facet_config[agg]] = excluded_filter
        return aggregate

    def _annotate_aggs_for_translation(self, es_search: Search):
        """
        Annotate the aggregations in the given Elasticsearch search request so we can later translate substitutes for
        None in the aggregations part of the response.
        """

        def annotate(agg: Agg):
            if isinstance(agg, Terms):
                path = agg.field.split('.')
                if path[-1] == 'keyword':
                    path.pop()
                if not hasattr(agg, 'meta'):
                    agg.meta = {}
                agg.meta['path'] = path
            if hasattr(agg, 'aggs'):
                subs = agg.aggs
                for sub_name in subs:
                    annotate(subs[sub_name])

        for agg_name in es_search.aggs:
            annotate(es_search.aggs[agg_name])

    def _translate_response_aggs(self, catalog: CatalogName, es_response: Response):
        """
        Translate substitutes for None in the aggregations part of an Elasticsearch response.
        """

        def translate(agg: AggResponse):
            if isinstance(agg, FieldBucketData):
                field_type = self.field_type(catalog, tuple(agg.meta['path']))
                for bucket in agg:
                    bucket['key'] = field_type.from_index(bucket['key'])
                    translate(bucket)
            elif isinstance(agg, BucketData):
                for attr in dir(agg):
                    value = getattr(agg, attr)
                    if isinstance(value, AggResponse):
                        translate(value)
            elif isinstance(agg, (FieldBucket, Bucket)):
                for sub in agg:
                    translate(sub)

        for agg in es_response.aggs:
            translate(agg)

    def _create_request(self,
                        catalog: CatalogName,
                        filters: Filters,
                        post_filter: bool = False,
                        source_filter: SourceFilters = None,
                        enable_aggregation: bool = True,
                        entity_type='files'):
        """
        This function will create an ElasticSearch request based on
        the filters and facet_config passed into the function
        :param filters: The 'filters' parameter.
        Assumes to be translated into es_key terms
        :param post_filter: Flag for doing either post_filter or regular
        querying (i.e. faceting or not)
        :param List source_filter: A list of "foo.bar" field paths (see
               https://www.elastic.co/guide/en/elasticsearch/reference/5.5/search-request-source-filtering.html)
        :param enable_aggregation: Flag for enabling query aggregation (and
               effectively ignoring facet configuration)
        :param entity_type: the string referring to the entity type used to get
        the ElasticSearch index to search
        :return: Returns the Search object that can be used for executing
        the request
        """
        service_config = self.service_config(catalog)
        field_mapping = service_config.translation
        facet_config = {key: field_mapping[key] for key in service_config.facets}
        es_search = Search(using=self.es_client, index=config.es_index_name(catalog=catalog,
                                                                            entity_type=entity_type,
                                                                            aggregate=True))
        filters = self._translate_filters(catalog, filters, field_mapping)

        es_query = self._create_query(catalog, filters)

        if post_filter:
            es_search = es_search.post_filter(es_query)
        else:
            es_search = es_search.query(es_query)

        if source_filter:
            es_search = es_search.source(include=source_filter)
        elif entity_type not in ("files", "bundles"):
            es_search = es_search.source(exclude="bundles")

        if enable_aggregation:
            for agg, translation in facet_config.items():
                es_search.aggs.bucket(agg, self._create_aggregate(catalog, filters, facet_config, agg))

        return es_search

    def _create_autocomplete_request(self,
                                     catalog: CatalogName,
                                     filters: Filters,
                                     es_client,
                                     _query,
                                     search_field,
                                     entity_type='files'):
        """
        This function will create an ElasticSearch request based on
        the filters passed to the function.

        :param catalog: The name of the catalog to create the ES request for.

        :param filters: The 'filters' parameter from '/keywords'.

        :param es_client: The ElasticSearch client object used to configure the
                          Search object

        :param _query: The query (string) to use for querying.

        :param search_field: The field to do the query on.

        :param entity_type: the string referring to the entity type used to get
                            the ElasticSearch index to search

        :return: Returns the Search object that can be used for executing the
                 request
        """
        service_config = self.service_config(catalog)
        field_mapping = service_config.autocomplete_translation[entity_type]
        es_search = Search(using=es_client,
                           index=config.es_index_name(catalog=catalog,
                                                      entity_type=entity_type,
                                                      aggregate=True))
        filters = self._translate_filters(catalog, filters, field_mapping)
        search_field = field_mapping[search_field] if search_field in field_mapping else search_field
        es_filter_query = self._create_query(catalog, filters)
        es_search = es_search.post_filter(es_filter_query)
        es_search = es_search.query(Q('prefix', **{str(search_field): _query}))
        return es_search

    def _apply_paging(self,
                      catalog: CatalogName,
                      es_search: Search,
                      pagination: Mapping[str, Any]):
        """
        Applies the pagination to the ES Search object
        :param catalog: The name of the catalog to query
        :param es_search: The ES Search object
        :param pagination: Dictionary with raw entries from the GET Request.
        It has: 'size', 'sort', 'order', and one of 'search_after', 'search_before', or 'from'.
        :return: An ES Search object where pagination has been applied
        """
        # Extract the fields for readability (and slight manipulation)

        _sort = pagination['sort'] + '.keyword'
        _order = pagination['order']

        field_type = self.field_type(catalog, tuple(pagination['sort'].split('.')))
        _mode = field_type.es_sort_mode

        def sort_values(sort_field, sort_order, sort_mode):
            assert sort_order in ('asc', 'desc'), sort_order
            return (
                {
                    sort_field: {
                        'order': sort_order,
                        'mode': sort_mode,
                        'missing': '_last' if sort_order == 'asc' else '_first'
                    }
                },
                {
                    '_uid': {
                        'order': sort_order
                    }
                }
            )

        # Using search_after/search_before pagination
        if 'search_after' in pagination:
            es_search = es_search.extra(search_after=pagination['search_after'])
            es_search = es_search.sort(*sort_values(_sort, _order, _mode))
        elif 'search_before' in pagination:
            es_search = es_search.extra(search_after=pagination['search_before'])
            rev_order = 'asc' if _order == 'desc' else 'desc'
            es_search = es_search.sort(*sort_values(_sort, rev_order, _mode))
        else:
            es_search = es_search.sort(*sort_values(_sort, _order, _mode))

        # fetch one more than needed to see if there's a "next page".
        es_search = es_search.extra(size=pagination['size'] + 1)
        return es_search

    def _generate_paging_dict(self, catalog: CatalogName, filters, es_response, pagination):
        """
        Generates the right dictionary for the final response.
        :param filters: The filters param from the request
        :param es_response: The raw dictionary response from ElasticSearch
        :param pagination: The pagination as coming from the GET request
        (or the defaults)
        :return: Modifies and returns the pagination updated with the
        new required entries.
        """

        def page_link(**kwargs) -> str:
            params = dict(catalog=catalog,
                          filters=json.dumps(filters),
                          sort=pagination['sort'],
                          order=pagination['order'],
                          size=pagination['size'],
                          **kwargs)
            return pagination['_self_url'] + '?' + urlencode(params)

        pages = -(-es_response['hits']['total'] // pagination['size'])

        # ...else use search_after/search_before pagination
        es_hits = es_response['hits']['hits']
        count = len(es_hits)
        if 'search_before' in pagination:
            # hits are reverse sorted
            if count > pagination['size']:
                # There is an extra hit, indicating a previous page.
                count -= 1
                search_before = es_hits[count - 1]['sort']
            else:
                # No previous page
                search_before = [None, None]
            search_after = es_hits[0]['sort']
        else:
            # hits are normal sorted
            if count > pagination['size']:
                # There is an extra hit, indicating a next page.
                count -= 1
                search_after = es_hits[count - 1]['sort']
            else:
                # No next page
                search_after = [None, None]
            if 'search_after' in pagination:
                search_before = es_hits[0]['sort']
            else:
                search_before = [None, None]

        # To return the type along with the value, return pagination variables
        # 'search_after' and 'search_before' as JSON formatted strings
        if search_after[1] is not None:
            search_after[0] = json.dumps(search_after[0])
        if search_before[1] is not None:
            search_before[0] = json.dumps(search_before[0])

        next_ = page_link(search_after=search_after[0],
                          search_after_uid=search_after[1]) if search_after[1] else None
        previous = page_link(search_before=search_before[0],
                             search_before_uid=search_before[1]) if search_before[1] else None
        page_field = {
            'count': count,
            'total': es_response['hits']['total'],
            'size': pagination['size'],
            'next': next_,
            'previous': previous,
            'pages': pages,
            'sort': pagination['sort'],
            'order': pagination['order']
        }

        return page_field

    def transform_summary(self,
                          catalog: CatalogName,
                          filters=None,
                          entity_type=None):
        if not filters:
            filters = {}
        es_search = self._create_request(catalog=catalog,
                                         filters=filters,
                                         post_filter=False,
                                         entity_type=entity_type)

        # Add a total file size aggregate
        es_search.aggs.metric(
            'total_size',
            'sum',
            field='contents.files.size_')

        # Add a cell count aggregate per organ
        es_search.aggs.bucket(
            'group_by_organ',
            'terms',
            field='contents.cell_suspensions.organ.keyword',
            size=config.terms_aggregation_size
        ).bucket(
            'cell_count',
            'sum',
            field='contents.cell_suspensions.total_estimated_cells_'
        )

        # Add a total cell count aggregate
        es_search.aggs.metric(
            'total_cell_count',
            'sum',
            field='contents.cell_suspensions.total_estimated_cells_'
        )

        # Add an organ aggregate to the Elasticsearch request
        es_search.aggs.bucket(
            'organTypes',
            'terms',
            field='contents.samples.effective_organ.keyword',
            size=config.terms_aggregation_size
        )

        for cardinality, agg_name in (
            ('contents.specimens.document_id', 'specimenCount'),
            ('contents.donors.genus_species', 'speciesCount'),
            ('contents.files.uuid', 'fileCount'),
            ('contents.donors.document_id', 'donorCount'),
            ('contents.projects.laboratory', 'labCount'),
            ('contents.projects.document_id', 'projectCount')
        ):
            es_search.aggs.metric(
                agg_name, 'cardinality',
                field=cardinality + '.keyword',
                precision_threshold="40000")

        self._annotate_aggs_for_translation(es_search)
        es_search = es_search.extra(size=0)
        es_response = es_search.execute(ignore_cache=True)
        assert len(es_response.hits) == 0
        self._translate_response_aggs(catalog, es_response)
        final_response = SummaryResponse(es_response.to_dict())
        if config.debug == 2 and logger.isEnabledFor(logging.DEBUG):
            logger.debug('Elasticsearch request: %s', json.dumps(es_search.to_dict(), indent=4))
        return final_response.return_response().to_json()

    def transform_request(self,
                          catalog: CatalogName,
                          filters=None,
                          pagination=None,
                          post_filter=False,
                          entity_type='files'):
        """
        This function does the whole transformation process. It takes
        the path of the config file, the filters, and
        pagination, if any. Excluding filters will do a match_all request.
        Excluding pagination will exclude pagination
        from the output.
        :param catalog: The name of the catalog to query
        :param filters: Filter parameter from the API to be used in the query.
        Defaults to None
        :param pagination: Pagination to be used for the API
        :param post_filter: Flag to indicate whether to do a post_filter
        call instead of the regular query.
        :param entity_type: the string referring to the entity type used to get
        the ElasticSearch index to search
        :return: Returns the transformed request
        """
        if not filters:
            filters = {}

        service_config = self.service_config(catalog)
        translation = service_config.translation
        inverse_translation = {v: k for k, v in translation.items()}

        for facet in filters.keys():
            if facet not in translation:
                raise BadArgumentException(f"Unable to filter by undefined facet {facet}.")

        facet = pagination["sort"]
        if facet not in translation:
            raise BadArgumentException(f"Unable to sort by undefined facet {facet}.")

        if post_filter is False:
            # No faceting (i.e. do the faceting on the filtered query)
            es_search = self._create_request(catalog=catalog,
                                             filters=filters,
                                             post_filter=False)
        else:
            # It's a full faceted search
            es_search = self._create_request(catalog=catalog,
                                             filters=filters,
                                             post_filter=post_filter,
                                             entity_type=entity_type)

        if pagination is None:
            # It's a single file search
            self._annotate_aggs_for_translation(es_search)
            es_response = es_search.execute(ignore_cache=True)
            self._translate_response_aggs(catalog, es_response)
            es_response_dict = es_response.to_dict()
            hits = [hit['_source'] for hit in es_response_dict['hits']['hits']]
            hits = self.translate_fields(catalog, hits, forward=False)
            final_response = KeywordSearchResponse(hits, entity_type)
        else:
            # It's a full file search
            # Translate the sort field if there is any translation available
            if pagination['sort'] in translation:
                pagination['sort'] = translation[pagination['sort']]
            es_search = self._apply_paging(catalog, es_search, pagination)
            self._annotate_aggs_for_translation(es_search)
            try:
                es_response = es_search.execute(ignore_cache=True)
            except elasticsearch.NotFoundError as e:
                raise IndexNotFoundError(e.info["error"]["index"])
            except elasticsearch.RequestError as e:
                if one(e.info['error']['root_cause'])['reason'].startswith('No mapping found for'):
                    es_response = self.es_client.count(index=config.es_index_name(catalog=catalog,
                                                                                  entity_type=entity_type,
                                                                                  aggregate=True))
                    if es_response['count'] == 0:  # Count is zero for empty index
                        final_response = FileSearchResponse(hits={},
                                                            pagination={},
                                                            facets={},
                                                            entity_type=entity_type)
                        return final_response.apiResponse.to_json()
                raise e
            self._translate_response_aggs(catalog, es_response)
            es_response_dict = es_response.to_dict()
            # Extract hits and facets (aggregations)
            es_hits = es_response_dict['hits']['hits']
            # If the number of elements exceed the page size, then we fetched one too many
            # entries to determine if there is a previous or next page.  In that case,
            # return one fewer hit.
            list_adjustment = 1 if len(es_hits) > pagination['size'] else 0
            if 'search_before' in pagination:
                hits = reversed(es_hits[0:len(es_hits) - list_adjustment])
            else:
                hits = es_hits[0:len(es_hits) - list_adjustment]
            hits = [hit['_source'] for hit in hits]
            hits = self.translate_fields(catalog, hits, forward=False)

            facets = es_response_dict['aggregations'] if 'aggregations' in es_response_dict else {}
            pagination['sort'] = inverse_translation[pagination['sort']]
            paging = self._generate_paging_dict(catalog, filters, es_response_dict, pagination)
            final_response = FileSearchResponse(hits, paging, facets, entity_type)

        final_response = final_response.apiResponse.to_json()

        return final_response

    def transform_autocomplete_request(self,
                                       catalog: CatalogName,
                                       pagination,
                                       filters=None,
                                       _query='',
                                       search_field='fileId',
                                       entry_format='file'):
        """
        This function does the whole transformation process. It takes the path
        of the config file, the filters, and pagination, if any. Excluding
        filters will do a match_all request. Excluding pagination will exclude
        pagination from the output.

        :param catalog: The name of the catalog to transform the autocomplete
                        request for.

        :param filters: Filter parameter from the API to be used in the query

        :param pagination: Pagination to be used for the API

        :param _query: String query to use on the search

        :param search_field: Field to perform the search on

        :param entry_format: Tells the method which _type of entry format to use

        :return: Returns the transformed request
        """
        service_config = self.service_config(catalog)
        mapping_config = service_config.autocomplete_mapping_config
        # Get the right autocomplete mapping configuration
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug('Entry is: %s', entry_format)
            logger.debug('Printing the mapping_config: \n%s', json_pp(mapping_config))
        mapping_config = mapping_config[entry_format]
        if not filters:
            filters = {}

        entity_type = 'files' if entry_format == 'file' else 'donor'
        es_search = self._create_autocomplete_request(
            catalog,
            filters,
            self.es_client,
            _query,
            search_field,
            entity_type=entity_type)
        # Handle pagination
        logger.info('Handling pagination')
        pagination['sort'] = '_score'
        pagination['order'] = 'desc'
        es_search = self._apply_paging(catalog, es_search, pagination)
        # Executing ElasticSearch request
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug('Printing ES_SEARCH request dict:\n %s', json.dumps(es_search.to_dict()))
        es_response = es_search.execute(ignore_cache=True)
        es_response_dict = es_response.to_dict()
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug('Printing ES_SEARCH response dict:\n %s', json.dumps(es_response_dict))
        # Extracting hits
        hits = [x['_source'] for x in es_response_dict['hits']['hits']]
        # Generating pagination
        logger.debug('Generating pagination')
        paging = self._generate_paging_dict(catalog, filters, es_response_dict, pagination)
        # Creating AutocompleteResponse
        logger.info('Creating AutoCompleteResponse')
        final_response = AutoCompleteResponse(
            mapping_config,
            hits,
            paging,
            _type=entry_format)
        final_response = final_response.apiResponse.to_json()
        logger.info('Returning the final response for transform_autocomplete_request')
        return final_response

    def transform_cart_item_request(self,
                                    catalog: CatalogName,
                                    entity_type,
                                    filters=None,
                                    search_after=None,
                                    size=1000):
        """
        Create a query using the given filter used for cart item requests

        :param catalog: The name of the catalog to query
        :param entity_type: type of entities to write to the cart
        :param filters: Filters to apply to entities
        :param search_after: String indicating the start of the page to search
        :param size: Maximum size of each page returned
        :return: A page of ES hits matching the filters and the search_after pagination string
        """
        if not filters:
            filters = {}

        service_config = self.service_config(catalog)
        source_filter = list(chain(service_config.cart_item['bundles'],
                                   service_config.cart_item[entity_type]))

        es_search = self._create_request(catalog=catalog,
                                         filters=filters,
                                         entity_type=entity_type,
                                         post_filter=False,
                                         source_filter=source_filter,
                                         enable_aggregation=False).sort('_uid')
        if search_after is not None:
            es_search = es_search.extra(search_after=[search_after])
        es_search = es_search[:size]

        hits = es_search.execute().hits
        if len(hits) > 0:
            meta = hits[-1].meta
            next_search_after = f'{meta.doc_type}#{meta.id}'
        else:
            next_search_after = None

        return hits, next_search_after
