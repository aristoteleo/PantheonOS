"""Profile-owned loopback TLS; no OS trust changes or ambient CA discovery.

The profile owner must hold its lifetime lock before calling prepare_tls. A
durable issuer survives port changes and restarts; the server key is replaced on
each invocation. Only the public CA goes into prepared App trust configuration.
"""
from datetime import datetime, timedelta, timezone
import ipaddress
import os
from pathlib import Path
import secrets
import ssl
import stat

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _write_private(path, data):
    temporary = path.with_name('.tls-' + secrets.token_hex(12))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _key_bytes(key):
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _certificate(subject, issuer, key, now, days):
    return (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=days)))


def prepare_tls(root: Path):
    """Return public CA and server PEM paths, failing closed on damaged identity."""
    now = datetime.now(timezone.utc)
    identity = root / 'tls-issuer.pem'
    try:
        fd = os.open(identity, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        key = ed25519.Ed25519PrivateKey.generate()
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Pantheon local Fleet profile')])
        cert = (_certificate(name, name, key, now, 3650)
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False,
                    content_commitment=False, data_encipherment=False, key_agreement=False,
                    key_cert_sign=True, crl_sign=True, encipher_only=None, decipher_only=None), critical=True)
                .sign(key, None))
        _write_private(identity, _key_bytes(key) + cert.public_bytes(serialization.Encoding.PEM))
    else:
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_size > 16384):
                raise ValueError('Local Fleet TLS identity must be an owner-private regular file')
            data = stream.read(16385)
        try:
            key = serialization.load_pem_private_key(data, password=None)
            cert = x509.load_pem_x509_certificate(data)
            if (not isinstance(key, ed25519.Ed25519PrivateKey)
                    or key.public_key().public_bytes_raw() != cert.public_key().public_bytes_raw()
                    or cert.issuer != cert.subject
                    or not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
                    or cert.not_valid_before_utc > now
                    or cert.not_valid_after_utc <= now + timedelta(days=1)):
                raise ValueError
            key.public_key().verify(cert.signature, cert.tbs_certificate_bytes)
        except Exception:
            raise ValueError('Invalid or expired local Fleet TLS identity; inspect the existing profile') from None
    public = root / 'tls-ca.pem'
    _write_private(public, cert.public_bytes(serialization.Encoding.PEM))
    server_key = ed25519.Ed25519PrivateKey.generate()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Pantheon local Fleet Controller')])
    server_cert = (_certificate(subject, cert.subject, server_key, now,
                    min(30, (cert.not_valid_after_utc - now).days))
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                   .add_extension(x509.SubjectAlternativeName([
                       x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                   .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                   .sign(key, None))
    server = root / 'tls-server.pem'
    _write_private(server, _key_bytes(server_key) + server_cert.public_bytes(serialization.Encoding.PEM))
    return public, server


def trust_context(ca):
    """Use only this profile's CA; hostnames and certificate validity still apply."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cafile=str(ca))
    return context
