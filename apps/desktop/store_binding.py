"""Explicit Store identity for a Desktop App, separate from CLI login state."""
from dataclasses import dataclass, field
import ipaddress
import ssl
from urllib.parse import urlsplit


@dataclass(frozen=True)
class DesktopStoreBinding:
    origin: str
    token: str = field(default='', repr=False)
    tls_context: ssl.SSLContext | None = field(default=None, repr=False)

    def __post_init__(self):
        try:
            parsed = urlsplit(self.origin)
            port = parsed.port
            host = parsed.hostname
            local = host == 'localhost'
            if host and not local:
                try:
                    local = ipaddress.ip_address(host).is_loopback
                except ValueError:
                    pass
            valid = (bool(host) and parsed.scheme in ('https', 'http')
                     and (parsed.scheme == 'https' or local)
                     and parsed.username is None and parsed.password is None
                     and parsed.path in ('', '/') and not parsed.query and not parsed.fragment
                     and (port is None or 0 < port <= 65535)
                     and not any(c.isspace() for c in self.origin))
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError('Store requires an HTTPS origin (HTTP is allowed only on loopback)')
        if not isinstance(self.token, str) or any(c.isspace() for c in self.token):
            raise ValueError('Store token must be an explicit bearer token or empty for anonymous access')
        if self.tls_context is not None and not isinstance(self.tls_context, ssl.SSLContext):
            raise ValueError('Store TLS trust must be an SSLContext')
        object.__setattr__(self, 'origin', self.origin.rstrip('/'))
