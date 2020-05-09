"""
Chalice application module to receive and process DSS event notifications.
"""
from collections import (
    defaultdict
)
import http
import json
import logging
import time
from typing import (
    List,
    MutableMapping,
    Optional,
)
import uuid

import boto3
# noinspection PyPackageRequirements
import chalice
from chalice.app import SQSRecord
from dataclasses import (
    dataclass,
    replace,
)
from more_itertools import (
    chunked
)

from azul import (
    config,
    hmac,
)
from azul.azulclient import AzulClient
from azul.chalice import AzulChaliceApp
from azul.health import HealthController
from azul.indexer.transformer import EntityReference
from azul.logging import configure_app_logging
from azul.plugins import MetadataPlugin
from azul.types import JSON

log = logging.getLogger(__name__)


class IndexerApp(AzulChaliceApp):

    @property
    def health_controller(self):
        # Don't cache. Health controller is meant to be short-lived since it
        # applies it's own caching. If we cached the controller, we'd never
        # observe any changes in health.
        return HealthController(lambda_name='indexer')

    def __init__(self):
        super().__init__(app_name=config.indexer_name,
                         # see LocalAppTestCase.setUpClass()
                         unit_test=globals().get('unit_test', False))


app = IndexerApp()

configure_app_logging(app, log)

plugin = MetadataPlugin.load()


@app.route('/version', methods=['GET'], cors=True)
def version():
    from azul.changelog import compact_changes
    return {
        'git': config.lambda_git_status,
        'changes': compact_changes(limit=10)
    }


@app.route('/health', methods=['GET'], cors=True)
def health():
    return app.health_controller.health()


@app.route('/health/basic', methods=['GET'], cors=True)
def basic_health():
    return app.health_controller.basic_health()


@app.route('/health/cached', methods=['GET'], cors=True)
def cached_health():
    return app.health_controller.cached_health()


@app.route('/health/fast', methods=['GET'], cors=True)
def fast_health():
    return app.health_controller.fast_health()


@app.route('/health/{keys}', methods=['GET'], cors=True)
def health_by_key(keys: Optional[str] = None):
    return app.health_controller.custom_health(keys)


@app.schedule('rate(1 minute)', name=config.indexer_cache_health_lambda_basename)
def update_health_cache(_event: chalice.app.CloudWatchEvent):
    app.health_controller.update_cache()


@app.route('/', cors=True)
def hello():
    return {'Hello': 'World!'}


@app.route('/delete', methods=['POST'])
@app.route('/', methods=['POST'])
def post_notification():
    """
    Receive a notification event and queue it for indexing or deletion.
    """
    hmac.verify(current_request=app.current_request)
    notification = app.current_request.json_body
    log.info("Received notification %r", notification)
    validate_request_syntax(notification)
    if app.current_request.context['path'] == '/':
        return process_notification('add', notification)
    elif app.current_request.context['path'] in ('/delete', '/delete/'):
        return process_notification('delete', notification)
    else:
        assert False


def process_notification(action: str, notification: JSON):
    if config.test_mode:
        if 'test_name' not in notification:
            log.error('Rejecting non-test notification in test mode: %r.', notification)
            raise chalice.ChaliceViewError('The indexer is currently in test mode where it only accepts specially '
                                           'instrumented notifications. Please try again later')
    else:
        if 'test_name' in notification:
            log.error('Rejecting test notification in production mode: %r.', notification)
            raise chalice.BadRequestError('Cannot process test notifications outside of test mode')

    message = dict(action=action, notification=notification)
    notify_queue = queue(config.notify_queue_name)
    notify_queue.send_message(MessageBody=json.dumps(message))
    log.info("Queued notification %r", notification)
    return chalice.app.Response(body='', status_code=http.HTTPStatus.ACCEPTED)


def validate_request_syntax(notification):
    try:
        match = notification['match']
    except KeyError:
        raise chalice.BadRequestError('Missing notification entry: match')

    try:
        bundle_uuid = match['bundle_uuid']
    except KeyError:
        raise chalice.BadRequestError('Missing notification entry: bundle_uuid')

    try:
        bundle_version = match['bundle_version']
    except KeyError:
        raise chalice.BadRequestError('Missing notification entry: bundle_version')

    if not isinstance(bundle_uuid, str):
        raise chalice.BadRequestError(f'Invalid type: bundle_uuid: {type(bundle_uuid)} (should be str)')

    if not isinstance(bundle_version, str):
        raise chalice.BadRequestError(f'Invalid type: bundle_version: {type(bundle_version)} (should be str)')

    if bundle_uuid.lower() != str(uuid.UUID(bundle_uuid)).lower():
        raise chalice.BadRequestError(f'Invalid syntax: {bundle_uuid} (should be a UUID)')

    if not bundle_version:
        raise chalice.BadRequestError('Invalid syntax: bundle_version can not be empty')


# Work around https://github.com/aws/chalice/issues/856

def new_handler(self, event, context):
    app.lambda_context = context
    return old_handler(self, event, context)


old_handler = chalice.app.EventSourceHandler.__call__
chalice.app.EventSourceHandler.__call__ = new_handler


def queue(queue_name):
    return boto3.resource('sqs').get_queue_by_name(QueueName=queue_name)


@app.on_sqs_message(queue=config.notify_queue_name, batch_size=1)
def index(event: chalice.app.SQSEvent):
    for record in event:
        message = json.loads(record.body)
        attempts = record.to_dict()['attributes']['ApproximateReceiveCount']
        log.info(f'Worker handling message {message}, attempt #{attempts} (approx).')
        start = time.time()
        try:
            action = message['action']
            if action == 'reindex':
                AzulClient.do_remote_reindex(message)
            else:
                notification = message['notification']
                indexer_cls = plugin.indexer_class()
                indexer = indexer_cls()
                if action == 'add':
                    contributions = indexer.transform(notification, delete=False)
                elif action == 'delete':
                    contributions = indexer.transform(notification, delete=True)
                else:
                    assert False

                log.info("Writing %i contributions to index.", len(contributions))
                tallies = indexer.contribute(contributions)
                tallies = [DocumentTally.for_entity(entity, num_contributions)
                           for entity, num_contributions in tallies.items()]

                log.info("Queueing %i entities for aggregating a total of %i contributions.",
                         len(tallies), sum(tally.num_contributions for tally in tallies))
                document_queue = queue(config.document_queue_name)
                for batch in chunked(tallies, document_batch_size):
                    document_queue.send_messages(Entries=[dict(tally.to_message(), Id=str(i))
                                                          for i, tally in enumerate(batch)])
        except BaseException:
            log.warning(f"Worker failed to handle message {message}.", exc_info=True)
            raise
        else:
            duration = time.time() - start
            log.info(f'Worker successfully handled message {message} in {duration:.3f}s.')


# The number of documents to be queued in a single SQS `send_messages`. Theoretically, larger batches are better but
# SQS currently limits the batch size to 10.
#
document_batch_size = 10


@app.on_sqs_message(queue=config.document_queue_name, batch_size=document_batch_size)
def write(event: chalice.app.SQSEvent):
    document_queue = queue(config.document_queue_name)

    # Consolidate multiple tallies for the same entity and process entities with only one message. Because SQS FIFO
    # queues try to put as many messages from the same message group in a reception batch, a single message per
    # group may indicate that that message is the last one in the group. Inversely, multiple messages per group
    # in a batch are a likely indicator for the presence of even more queued messages in that group. The more
    # bundle contributions we defer, the higher the amortized savings on aggregation become. Aggregating bundle
    # contributions is a costly operation for any entity with many contributions e.g., a large project.
    #
    tallies_by_id: MutableMapping[EntityReference, List[DocumentTally]] = defaultdict(list)

    for record in event:
        tally = DocumentTally.from_sqs_record(record)
        log.info('Attempt %i of handling %i contribution(s) for entity %s/%s',
                 tally.attempts, tally.num_contributions, tally.entity.entity_type, tally.entity.entity_id)
        tallies_by_id[tally.entity].append(tally)
    deferrals, referrals = [], []
    for tallies in tallies_by_id.values():
        if len(tallies) == 1:
            referrals.append(tallies[0])
        elif len(tallies) > 1:
            deferrals.append(tallies[0].consolidate(tallies[1:]))
        else:
            assert False

    if referrals:
        for tally in referrals:
            log.info('Aggregating %i contribution(s) to entity %s/%s',
                     tally.num_contributions, tally.entity.entity_type, tally.entity.entity_id)
        indexer_cls = plugin.indexer_class()
        indexer = indexer_cls()
        tallies = {tally.entity: tally.num_contributions for tally in referrals}
        indexer.aggregate(tallies)

    if deferrals:
        for tally in deferrals:
            log.info('Deferring aggregation of %i contribution(s) to entity %s/%s',
                     tally.num_contributions, tally.entity.entity_type, tally.entity.entity_id)
        document_queue.send_messages(Entries=[dict(tally.to_message(), Id=str(i))
                                              for i, tally in enumerate(deferrals)])


@dataclass(frozen=True)
class DocumentTally:
    """
    Tracks the number of bundle contributions to a particular metadata entity.

    Each instance represents a message in the document queue.
    """
    entity: EntityReference
    num_contributions: int
    attempts: int

    @classmethod
    def from_sqs_record(cls, record: SQSRecord) -> 'DocumentTally':
        body = json.loads(record.body)
        attributes = record.to_dict()['attributes']
        return cls(entity=EntityReference(entity_type=body['entity_type'],
                                          entity_id=body['entity_id']),
                   num_contributions=body['num_contributions'],
                   attempts=int(attributes['ApproximateReceiveCount']))

    @classmethod
    def for_entity(cls, entity: EntityReference, num_contributions: int) -> 'DocumentTally':
        return cls(entity=entity,
                   num_contributions=num_contributions,
                   attempts=0)

    def to_json(self) -> JSON:
        return {
            'entity_type': self.entity.entity_type,
            'entity_id': self.entity.entity_id,
            'num_contributions': self.num_contributions
        }

    def to_message(self) -> JSON:
        return dict(MessageBody=json.dumps(self.to_json()),
                    MessageGroupId=self.entity.entity_id,
                    MessageDeduplicationId=str(uuid.uuid4()))

    def consolidate(self, others: List['DocumentTally']) -> 'DocumentTally':
        assert all(self.entity == other.entity for other in others)
        return replace(self, num_contributions=sum((other.num_contributions for other in others),
                                                   self.num_contributions))
