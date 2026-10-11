from server.src.llm_auth_errors import (
    describe_gateway_failure, is_non_retryable_gateway_auth_failure,
)


class GatewayError(Exception):
    def __init__(self, message: str, body=None, status_code=None):
        super().__init__(message)
        self.body = body
        self.status_code = status_code


def test_9router_access_denied_is_not_misidentified_as_virtual_key():
    error = GatewayError(
        "Error code: 401 - {'type': 'access_not_found', 'is_bifrost_error': False, "
        "'status_code': 401, 'extra_fields': {'provider': '9router', "
        "'error_type': 'policy_access_denied'}}",
        status_code=401,
    )
    text = describe_gateway_failure(error)
    assert '9Router credential rejected' in text
    assert 'upstream' in text
    assert 'revoked 9Router' in text
    assert 'Bifrost rejected Trajecta' not in text
    assert is_non_retryable_gateway_auth_failure(error)


def test_structured_downstream_failure():
    error = GatewayError('401', body={
        'type': 'access_not_found', 'is_bifrost_error': False, 'status_code': 401,
        'extra_fields': {'provider': '9router', 'error_type': 'policy_access_denied'},
    })
    assert '9Router credential rejected' in describe_gateway_failure(error)


def test_bifrost_key_rejection_has_distinct_message():
    error = GatewayError('Error code: 401 invalid virtual key', status_code=401)
    assert 'Bifrost rejected Trajecta' in describe_gateway_failure(error)
    assert is_non_retryable_gateway_auth_failure(error)


def test_other_errors_unchanged():
    error = GatewayError('Service unavailable')
    assert describe_gateway_failure(error) == 'Service unavailable'
    assert not is_non_retryable_gateway_auth_failure(error)
