import io
import json
from unittest.mock import patch

import pytest

from pantheon.apps.dependency_client import DependencyClient, DependencyCallError
from pantheon.apps.runtime_config import RuntimeCredential


class Connection:
    def __init__(self, status=200, raw=b'{"success":true,"result":"hello"}'):
        self.status, self.raw = status, raw
        self.calls = []
        self.closed = False

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))

    def getresponse(self):
        return self

    def read(self, n):
        return io.BytesIO(self.raw).read(n)

    def close(self):
        self.closed = True


def test_dependency_client_uses_only_explicit_credential(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'https://unrelated-proxy.test')
    monkeypatch.setenv('FLEET_KEY', 'master-must-not-leak')
    credential = RuntimeCredential('https://instance.apps.test/rpc', 'a' * 64)
    client = DependencyClient(credential)
    connection = Connection()
    with patch('http.client.HTTPSConnection', return_value=connection) as factory:
        assert client.invoke('read_file', {'path': 'data.csv'}, timeout_seconds=5)['result'] == 'hello'
    assert connection.closed
    assert len(connection.calls) == 1
    factory.assert_called_once_with('instance.apps.test', None, context=None, timeout=10)
    positional, kwargs = connection.calls[0]
    assert positional == ('POST', '/rpc')
    assert kwargs['headers'] == {'Authorization': 'Bearer ' + 'a' * 64, 'Content-Type': 'application/json'}
    assert json.loads(kwargs['body']) == {'method': 'read_file', 'args': {'path': 'data.csv'}, 'timeout_seconds': 5}
    assert credential.key not in repr(client)
    assert 'master' not in str(connection.calls)


@pytest.mark.parametrize('status,unknown', [(401, False), (403, False), (409, False), (307, True), (502, True)])
def test_dependency_client_never_retries_or_follows_redirects(status, unknown):
    connection = Connection(status, b'secret-provider-error')
    with patch('http.client.HTTPSConnection', return_value=connection):
        with pytest.raises(DependencyCallError) as result:
            DependencyClient(RuntimeCredential('https://instance.apps.test/rpc', 'a' * 64)).invoke('mutate')
    assert len(connection.calls) == 1 and connection.closed
    assert result.value.status == status and result.value.outcome_unknown == unknown
    assert 'secret-provider-error' not in str(result.value)


@pytest.mark.parametrize('endpoint', ['http://localhost/rpc', 'https://a.test/admin', 'https://a.test/rpc?path=admin', 'https://user:pass@a.test/rpc'])
def test_dependency_client_rejects_unpinned_endpoint(endpoint):
    with pytest.raises(ValueError):
        DependencyClient(RuntimeCredential(endpoint, 'a' * 64))


@pytest.mark.parametrize('raw', [b'x', b' ' * (512 * 1024 + 1)])
def test_dependency_invalid_result_is_unknown_not_replayed(raw):
    connection = Connection(raw=raw)
    with patch('http.client.HTTPSConnection', return_value=connection):
        with pytest.raises(DependencyCallError) as result:
            DependencyClient(RuntimeCredential('https://instance.apps.test/rpc', 'a' * 64)).invoke('mutate')
    assert result.value.outcome_unknown and len(connection.calls) == 1 and connection.closed
