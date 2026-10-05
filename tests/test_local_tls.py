import os
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

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
    assert context.verify_flags & ssl.VERIFY_X509_STRICT
    assert context.cert_store_stats()['x509_ca'] == 1
    cert = x509.load_pem_x509_certificate(server.read_bytes())
    sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert [str(ip) for ip in sans.get_values_for_type(x509.IPAddress)] == ['127.0.0.1']
    strict_handshake(ca, server)


def strict_handshake(ca, certificate):
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate)
    client_in, client_out, server_in, server_out = (ssl.MemoryBIO() for _ in range(4))
    client = trust_context(ca).wrap_bio(client_in, client_out, server_hostname='127.0.0.1')
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    done = set()
    for _ in range(20):
        for name, peer, output, destination in (
                ('client', client, client_out, server_in), ('server', server, server_out, client_in)):
            try:
                peer.do_handshake()
                done.add(name)
            except ssl.SSLWantReadError:
                pass
            if output.pending:
                destination.write(output.read())
        if len(done) == 2:
            return
    pytest.fail('Strict local TLS handshake did not finish')


def test_early_issuer_metadata_upgrade_preserves_identity(tmp_path):
    ca, server = prepare_tls(tmp_path)
    path = tmp_path / 'tls-issuer.pem'
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    original = x509.load_pem_x509_certificate(ca.read_bytes())
    builder = (x509.CertificateBuilder().subject_name(original.subject).issuer_name(original.issuer)
               .public_key(key.public_key()).serial_number(original.serial_number)
               .not_valid_before(original.not_valid_before_utc).not_valid_after(original.not_valid_after_utc))
    for extension in original.extensions:
        if not isinstance(extension.value, x509.SubjectKeyIdentifier):
            builder = builder.add_extension(extension.value, extension.critical)
    early = builder.sign(key, None)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    path.write_bytes(private + early.public_bytes(serialization.Encoding.PEM))
    prepare_tls(tmp_path)
    upgraded = x509.load_pem_x509_certificate(ca.read_bytes())
    assert upgraded.public_key().public_bytes_raw() == early.public_key().public_bytes_raw()
    assert upgraded.subject == early.subject and upgraded.serial_number == early.serial_number
    assert upgraded.not_valid_after_utc == early.not_valid_after_utc
    assert upgraded.extensions.get_extension_for_class(x509.BasicConstraints) == early.extensions.get_extension_for_class(x509.BasicConstraints)
    assert path.read_bytes().startswith(private)
    strict_handshake(ca, server)
    stable = path.read_bytes()
    prepare_tls(tmp_path)
    assert path.read_bytes() == stable


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
