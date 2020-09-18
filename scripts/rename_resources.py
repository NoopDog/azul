import logging

from azul.deployment import (
    terraform,
)
from azul.logging import (
    configure_script_logging,
)

log = logging.getLogger(__name__)

renamed = {
    'aws_cloudwatch_log_group.azul_error_log': 'aws_cloudwatch_log_group.error_log',
    'aws_cloudwatch_log_group.azul_index_log': 'aws_cloudwatch_log_group.index_log',
    'aws_cloudwatch_log_group.azul_search_log': 'aws_cloudwatch_log_group.search_log',
    'aws_cloudwatch_log_resource_policy.azul_es_cloudwatch_policy': 'aws_cloudwatch_log_resource_policy.index',
    'aws_dynamodb_table.cart_items_table': 'aws_dynamodb_table.cart_items',
    'aws_dynamodb_table.carts_table': 'aws_dynamodb_table.carts',
    'aws_dynamodb_table.users_table': 'aws_dynamodb_table.users',
    'aws_dynamodb_table.versions_table': 'aws_dynamodb_table.object_versions',
    'aws_elasticsearch_domain.elasticsearch[0]': 'aws_elasticsearch_domain.index',
    'aws_iam_role.state_machine_iam_role': 'aws_iam_role.states',
    'aws_iam_role_policy.state_machine_iam_policy': 'aws_iam_role_policy.states',
    'aws_s3_bucket.bucket': 'aws_s3_bucket.storage',
    'aws_s3_bucket.url_bucket': 'aws_s3_bucket.urls',
    'aws_sfn_state_machine.cart_export_state_machine': 'aws_sfn_state_machine.cart_export',
    'aws_sfn_state_machine.cart_item_state_machine': 'aws_sfn_state_machine.cart_item',
    'aws_sfn_state_machine.manifest_state_machine': 'aws_sfn_state_machine.manifest',
    'google_service_account.indexer': 'google_service_account.serviceaccount',
    'null_resource.hmac-secret': 'null_resource.hmac_secret'
}

rename_chalice = [
    'module.chalice_service.aws_cloudwatch_event_rule.indexercachehealth',
    'module.chalice_service.aws_cloudwatch_event_target.indexercachehealth',
    'module.chalice_service.aws_lambda_permission.indexercachehealth',
    'module.chalice_service.aws_cloudwatch_event_rule.servicecachehealth',
    'module.chalice_service.aws_cloudwatch_event_target.servicecachehealth',
    'module.chalice_service.aws_lambda_permission.servicecachehealth'
]
renamed.update({name + '-event': name for name in rename_chalice})


def main():
    current_names = terraform.run('state', 'list').splitlines()
    for current_name in current_names:
        try:
            new_name = renamed[current_name]
        except KeyError:
            if current_name in renamed.values():
                log.info('Found %r, already renamed', current_name)
        else:
            log.info('Found %r, renaming to %r', current_name, new_name)
            terraform.run('state', 'mv', current_name, new_name)


if __name__ == '__main__':
    configure_script_logging(log)
    main()
