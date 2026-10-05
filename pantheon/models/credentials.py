"""HTTP model credential compatibility over the ordinary App credential vault.

Keep model API validation and legacy imports stable. Bus credentials are accepted
only by the generic App provisioning classes, never by model configuration.
"""
from urllib.parse import urlsplit

from pantheon.apps.credentials import LocalAppCredentialVault, RemoteAppCredentialVault, _regular_file


def model_credential_endpoint(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(c.isspace() for c in value):
        raise ValueError('Supply an explicit model API endpoint')
    parts = urlsplit(value)
    if (not parts.hostname or parts.username or parts.password or '?' in value or '#' in value
            or parts.scheme not in ('http', 'https')
            or parts.scheme == 'http' and parts.hostname not in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('Model credentials require HTTPS or a local endpoint')
    parts.port
    # The vault normalizes its lookup identity; a native SDK's actual API base
    # must retain the source path. Adding /v1 here changes the request URL.
    return value.rstrip('/')


class LocalModelCredentialVault(LocalAppCredentialVault):
    validate_endpoint = staticmethod(model_credential_endpoint)


class RemoteModelCredentialVault(RemoteAppCredentialVault):
    validate_endpoint = staticmethod(model_credential_endpoint)
