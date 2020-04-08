from argparse import (
    ArgumentParser,
)
import json
import logging
from pathlib import (
    Path,
)
import re
import shutil
import sys
import typing

from azul import (
    config,
)
from azul.deployment import (
    populate_tags,
)
from azul.files import (
    write_file_atomically,
)
from azul.logging import (
    configure_script_logging,
)
from azul.types import (
    AnyJSON,
    JSON,
    PrimitiveJSON,
)

log = logging.getLogger(__name__)
T = typing.TypeVar('T', *PrimitiveJSON.__args__)  # noqa
U = typing.TypeVar('U', *AnyJSON.__args__)  # noqa


def transform_tf(input_json):
    # Using the default provider makes switching deployments easier
    del input_json['provider']

    assert 'variable' not in input_json
    input_json['variable'] = {
        'role_arn': {},
        'layer_arn': {},
        'es_endpoint': {},
        'es_instance_count': {}
    }

    input_json['output']['rest_api_id'] = {
        'value': '${aws_api_gateway_rest_api.rest_api.id}'
    }

    for func in input_json['resource']['aws_lambda_function'].values():
        assert 'layers' not in func
        func['layers'] = ["${var.layer_arn}"]

        # Inject ES-specific environment from variables set by Terraform.
        for var, val in config.es_endpoint_env(
            es_endpoint=('${var.es_endpoint[0]}', '${var.es_endpoint[1]}'),
            es_instance_count='${var.es_instance_count}'
        ).items():
            func['environment']['variables'][var] = val

    def patch_cloudwatch_resource(resource_type_name, property_name):
        # Currently, Chalice fails to prefix the names of some resources. We
        # need them to be prefixed with `azul-` to allow for limiting the
        # scope of certain IAM permissions for Gitlab and, more importantly,
        # the deployment stage so these resources are segregated by deployment.
        for resource in input_json['resource'][resource_type_name].values():
            function_name, _, suffix = resource[property_name].partition('-')
            assert suffix == 'event', suffix
            assert function_name, function_name
            resource[property_name] = config.qualified_resource_name(function_name)

    patch_cloudwatch_resource('aws_cloudwatch_event_rule', 'name')
    patch_cloudwatch_resource('aws_cloudwatch_event_target', 'target_id')

    return input_json


def patch_resource_names(tf_config: JSON) -> JSON:
    """
    Some Chalice-generated resources have named ending in `-event`. The
    dash prevents generation of a fully qualified resource name (i.e.,
    with `config.qualified_resource_name`. This function returns
    Terraform configuration with names and references updated to
    remove the trailing `-event`.

    >>> from azul.doctests import assert_json
    >>> assert_json(patch_resource_names({
    ...     'resource': {
    ...         'aws_cloudwatch_event_rule': {
    ...             'indexercachehealth-event': {
    ...                'foo': 'abc'
    ...             },
    ...             'servicecachehealth-event': {
    ...                 'bar': '${aws_cloudwatch_event_rule.indexercachehealth-event.foo}'
    ...             }
    ...         }
    ...     }
    ... }))
    {
        "resource": {
            "aws_cloudwatch_event_rule": {
                "indexercachehealth": {
                    "foo": "abc"
                },
                "servicecachehealth": {
                    "bar": "${aws_cloudwatch_event_rule.indexercachehealth.foo}"
                }
            }
        }
    }


    >>> assert_json(patch_resource_names({
    ...     'resource': {
    ...         'unaffected_resource_type': {
    ...             'unaffected_resource_name': {}
    ...         }
    ...     }
    ... }))
    {
        "resource": {
            "unaffected_resource_type": {
                "unaffected_resource_name": {}
            }
        }
    }
    """

    def _patch_name(resource_name: str) -> str:
        if resource_name.endswith('-event'):
            return resource_name[:-len('-event')]
        else:
            return resource_name

    def _replace_refs(value: T) -> T:
        if isinstance(value, str):
            pattern = re.compile(r'\${(\w+\.\w+)(?:-event)(\.\w+)}')
            return pattern.sub(r'${\1\2}', value)
        else:
            return value

    def _patch_refs(block: U) -> U:
        assert not isinstance(block, typing.Sequence)
        return {
            k: _patch_refs(v) if isinstance(v, typing.Mapping) else _replace_refs(v)
            for k, v in block.items()
        }

    return {
        k: v if k != 'resource' else {
            resource_type: {
                _patch_name(resource_name): _patch_refs(arguments)
                for resource_name, arguments in resource.items()
            }
            for resource_type, resource in v.items()
        }
        for k, v in tf_config.items()
    }


def main(argv):
    parser = ArgumentParser(
        description='Prepare the Terraform config generated by `chalice package'
                    '--pkg-format terraform` and copy it into the terraform/ '
                    'directory.'
    )
    parser.add_argument('lambda_name', help='the lambda of the config that will be '
                                            'transformed and copied')
    options = parser.parse_args(argv)
    source_dir = Path(config.project_root) / 'lambdas' / options.lambda_name / '.chalice' / 'terraform'
    output_dir = Path(config.project_root) / 'terraform' / options.lambda_name
    output_dir.mkdir(exist_ok=True)

    deployment_src = source_dir / 'deployment.zip'
    deployment_dst = output_dir / 'deployment.zip'
    log.info('Copying %s to %s', deployment_src, deployment_dst)
    shutil.copyfile(deployment_src, deployment_dst)

    tf_src = source_dir / 'chalice.tf.json'
    tf_dst = output_dir / 'chalice.tf.json'
    log.info('Transforming %s to %s', tf_src, tf_dst)
    with open(tf_src, 'r') as f:
        output_json = json.load(f)
    output_json = populate_tags(patch_resource_names(transform_tf(output_json)))
    with write_file_atomically(tf_dst) as f:
        json.dump(output_json, f, indent=4)


if __name__ == '__main__':
    configure_script_logging(log)
    main(sys.argv[1:])
