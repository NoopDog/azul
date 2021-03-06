from contextlib import (
    contextmanager,
)
from functools import (
    lru_cache,
)
import json
import logging
import os
import re
import tempfile
import threading
from typing import (
    Mapping,
    Optional,
)
from unittest.mock import (
    patch,
)

import boto3
import botocore.session

from azul import (
    Netloc,
    cached_property,
    config,
)
from azul.template import (
    emit,
)
from azul.types import (
    JSON,
)

log = logging.getLogger(__name__)


class AWS:
    class _PerThread(threading.local):
        session: Optional[boto3.session.Session] = None

    def __init__(self) -> None:
        super().__init__()
        self._per_thread = self._PerThread()

    @cached_property
    def profile(self):
        session = botocore.session.Session()
        profile_name = session.get_config_variable('profile')
        return {} if profile_name is None else session.full_config['profiles'][profile_name]

    @cached_property
    def region_name(self):
        return self.sts.meta.region_name

    @cached_property
    def sts(self):
        return boto3.client('sts')

    @cached_property
    def lambda_(self):
        return boto3.client('lambda')

    @cached_property
    def apigateway(self):
        return boto3.client('apigateway')

    @cached_property
    def account(self):
        assert self.sts.get_caller_identity()['Account'] == config.aws_account_id
        return self.sts.get_caller_identity()['Account']

    @cached_property
    def es(self):
        return boto3.client('es')

    @cached_property
    def stepfunctions(self):
        return boto3.client('stepfunctions')

    @cached_property
    def iam(self):
        return boto3.client('iam')

    @cached_property
    def secretsmanager(self):
        return boto3.client('secretsmanager')

    @lru_cache(maxsize=1)
    def dynamo(self, endpoint_url, region_name):
        return boto3.resource('dynamodb', endpoint_url=endpoint_url, region_name=region_name)

    def api_gateway_export(self, gateway_id):
        response = self.apigateway.get_export(restApiId=gateway_id,
                                              stageName=config.deployment_stage,
                                              exportType='oas30',
                                              accepts='application/json')
        return json.load(response['body'])

    @property
    def es_endpoint(self) -> Optional[Netloc]:
        if config.es_endpoint:
            return config.es_endpoint
        else:
            return self._es_domain_status['Endpoint'], 443

    @property
    def es_instance_count(self) -> Optional[int]:
        if config.es_endpoint:
            return config.es_instance_count
        else:
            return self._es_domain_status['ElasticsearchClusterConfig']['InstanceCount']

    @cached_property
    def _es_domain_status(self) -> Optional[JSON]:
        """
        Return the status of the current deployment's Elasticsearch domain
        """
        es_domain = self.es.describe_elasticsearch_domain(DomainName=config.es_domain)
        return es_domain['DomainStatus']

    def get_lambda_arn(self, function_name, suffix):
        return f"arn:aws:lambda:{self.region_name}:{self.account}:function:{function_name}-{suffix}"

    @cached_property
    def permissions_boundary_arn(self) -> str:
        return f'arn:aws:iam::{self.account}:policy/{config.permissions_boundary_name}'

    @cached_property
    def permissions_boundary(self):
        try:
            return self.iam.get_policy(PolicyArn=self.permissions_boundary_arn)['Policy']
        except self.iam.exceptions.NoSuchEntityException:
            return None

    @cached_property
    def permissions_boundary_tf(self) -> Mapping[str, str]:
        return {} if self.permissions_boundary is None else {
            'permissions_boundary': self.permissions_boundary['Arn']
        }

    def get_hmac_key_and_id(self):
        # Note: dict contains 'key' and 'key_id' as keys and is provisioned in scripts/provision_credentials.py
        response = self.secretsmanager.get_secret_value(SecretId=config.secrets_manager_secret_name('indexer', 'hmac'))
        secret_dict = json.loads(response['SecretString'])
        return secret_dict['key'], secret_dict['key_id']

    @lru_cache()
    def get_hmac_key_and_id_cached(self, cache_key_id):
        key, key_id = self.get_hmac_key_and_id()
        assert cache_key_id == key_id
        return key, key_id

    def dss_main_bucket(self, dss_endpoint: str) -> str:
        return self._dss_bucket(dss_endpoint, lambda_name='indexer')

    # Remove once https://github.com/HumanCellAtlas/data-store/issues/1837 is resolved

    def dss_checkout_bucket(self, dss_endpoint: str) -> str:
        return self._dss_bucket(dss_endpoint, 'checkout', lambda_name='service')

    @lru_cache()
    def _dss_bucket(self, dss_endpoint: str, *qualifiers: str, lambda_name: str) -> str:
        with self.direct_access_credentials(dss_endpoint, lambda_name):
            stage = config.dss_deployment_stage(dss_endpoint)
            name = f'/dcp/dss/{stage}/environment'
            # FIXME: make region configurable (https://github.com/DataBiosphere/azul/issues/1560)
            ssm = aws.client('ssm', region_name='us-east-1')
            dss_parameter = ssm.get_parameter(Name=name)
        dss_config = json.loads(dss_parameter['Parameter']['Value'])
        bucket_key = '_'.join(['dss', 's3', *qualifiers, 'bucket']).upper()
        return dss_config[bucket_key]

    @lru_cache
    def _service_account_creds(self, secret_name: str) -> JSON:
        sm = self.secretsmanager
        creds = sm.get_secret_value(SecretId=secret_name)
        return creds

    @contextmanager
    def service_account_credentials(self):
        """
        A context manager that patches the GOOGLE_APPLICATION_CREDENTIALS
        environment variable to point to a file containing the credentials of
        the Google service account that represents the Azul deployment. The
        returned context is the name of a temporary file containing the
        credentials.
        """
        secret_name = config.secrets_manager_secret_name('google_service_account')
        secret = self._service_account_creds(secret_name)['SecretString']
        with tempfile.NamedTemporaryFile(mode='w+') as f:
            f.write(secret)
            f.flush()
            with patch.dict(os.environ, GOOGLE_APPLICATION_CREDENTIALS=f.name):
                yield f.name

    def direct_access_credentials(self, dss_endpoint: str, lambda_name: str):
        """
        A context manager that causes the client() method to return boto3
        clients that use credentials suitable for accessing the DSS bucket
        directly.

        :param dss_endpoint: The URL of the REST API endpoint of the DSS
                             instance whose bucket is to be accessed directly

        :param lambda_name: The name of the lambda wishing to access the bucket
                            directly. If direct access is gained by assuming a
                            role and if the role name is parameterized with the
                            lambda name, the specified value will be
                            interpolated into the role name. See
                            AZUL_DSS_DIRECT_ACCESS_ROLE for details
        """
        if dss_endpoint == config.dss_endpoint:
            role_arn = config.dss_direct_access_role(lambda_name)
        else:
            role_arn = None
        return self.assumed_role_credentials(role_arn)

    @contextmanager
    def assumed_role_credentials(self, role_arn: Optional[str]):
        """
        A context manager that causes the client() method to return boto3
        clients that use credentials obtained by assuming the given role as long
        as that method is invoked in context i.e., the body of the `with`
        statement.

        This context manager is thread-safe in that it doesn't affect clients
        obtained by other threads, even when the context manager is active in
        one thread.

        It can be nested as long as the outer context's role has permission to
        assume the inner context's role. It is not reentrant in that two nested
        contexts cannot use the same role, since a role cannot assume itself.

        The given role is assumed using currently active credentials, either the
        the default ones or those from another assumed_role_credentials context.

        :param role_arn: the ARN of the role to assume. If None, the context
                         manager does nothing and calls to the .client() method
                         in context will use the same credentials as calls out
                         of context
        """
        if role_arn is None:
            # FIXME: make this CM reentrant by taking this branch if the given
            #        role is already assumed
            yield
        else:
            sts = self.client('sts')
            identity = sts.get_caller_identity()
            # If we used the current identity's ARN to derive the session name,
            # we'd quickly risk exceeding the maximum 64 character limit on the
            # session name, especially when nesting this context manager.
            # Instead we use the user ID, something like AKIAIOSFODNN7EXAMPLE),
            # as the session name and log it along with the ARN. That way we
            # can at least string things back together forensically.
            session_name = self.invalid_session_name_re.sub('.', identity['UserId'])
            log.info('Identity %s with ARN %s is about to assume role %s using session name %s.',
                     identity['UserId'], identity['Arn'], role_arn, session_name)
            response = sts.assume_role(RoleArn=role_arn,
                                       RoleSessionName=session_name)
            credentials = response['Credentials']
            new_session = boto3.session.Session(aws_access_key_id=credentials['AccessKeyId'],
                                                aws_secret_access_key=credentials['SecretAccessKey'],
                                                aws_session_token=credentials['SessionToken'])
            old_session = self._per_thread.session
            self._per_thread.session = new_session
            try:
                yield
            finally:
                self._per_thread.session = old_session

    invalid_session_name_re = re.compile(r'[^\w+=,.@-]')

    def client(self, *args, **kwargs):
        """
        Outside of a context established by `.assumed_role_credentials()` or
        `.direct_access_credentials()` this is the same as boto3.client. Within
        such a context, it returns boto3 clients that use different, temporary
        credentials.
        """
        session = self._per_thread.session or boto3
        return session.client(*args, **kwargs)

    def resource(self, *args, **kwargs):
        """
        Outside of a context established by `.assumed_role_credentials()` or
        `.direct_access_credentials()` this is the same as boto3.resource.
        Within such a context, it returns boto3 clients that use different,
        temporary credentials.
        """
        session = self._per_thread.session or boto3
        return session.resource(*args, **kwargs)


aws = AWS()


def _sanitize_tf(tf_config: JSON) -> JSON:
    """
    Avoid errors like

        Error: Missing block label

          on api_gateway.tf.json line 12:
          12:     "resource": []

        At least one object property is required, whose name represents the resource
        block's type.
    """
    return {k: v for k, v in tf_config.items() if v}


def emit_tf(tf_config: Optional[JSON]):
    return emit(tf_config) if tf_config is None else emit(_sanitize_tf(tf_config))
