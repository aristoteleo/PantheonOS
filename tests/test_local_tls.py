import os
import ssl

import pytest
from cryptography import x509

from pantheon.platform.local_tls import prepare_tls, trust_context


def test_local_tls_preserves_issuer_and_rotates_only_server(tmp_path):
    ca, server = prepare_tls(tmp_path)
    first = ca.read_bytes(), server.read_bytes()
    issuer = (tmp_path / 'tls-issuer.pem').read_bytes()
    prepare_tls(tmp_path)
    assert ca.read_bytes() == first[0]
    assert server.read_bytes() != first[1]
    assert (tmp_path / 'tls-issuer.pem').read_bytes() == issuer
    for path in tmp_path.iterdir():
        assert path.stat().st_mode & 0o077 == 0
    context = trust_context(ca)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert context.cert_store_stats()['x509_ca'] == 1
    cert = x509.load_pem_x509_certificate(server.read_bytes())
    sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert [str(ip) for ip in sans.get_values_for_type(x509.IPAddress)] == ['127.0.0.1']


@pytest.mark.parametrize('damage', ['corrupt', 'public', 'symlink', 'fifo'])
def test_local_tls_damaged_identity_never_replaced(tmp_path, damage):
    prepare_tls(tmp_path)
    path = tmp_path / 'tls-issuer.pem'
    original = path.read_bytes()
    if damage == 'corrupt':
        path.write_bytes(b'broken')
    elif damage == 'public':
        path.chmod(0o644)
    elif damage == 'symlink':
        target = tmp_path / 'other.pem'
        path.rename(target)
        path.symlink_to(target)
    else:
        path.unlink()
        os.mkfifo(path, 0o600)
    with pytest.raises((ValueError, OSError)):
        prepare_tls(tmp_path)
    if damage == 'corrupt':
        assert path.read_bytes() == b'broken'
    elif damage == 'public':
        assert path.read_bytes() == original
    elif damage == 'symlink':
        assert path.is_symlink()
