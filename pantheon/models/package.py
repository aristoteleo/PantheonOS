"""Bundle the existing Model Services consumer for an ordinary Python App.

This is source packaging, not another model stack. Provider engines, Agent,
settings, management credentials and provider-specific SDKs are excluded.
"""
from pathlib import Path
import shutil
import struct


CLIENT_MODULES = '''client dependency direct direct_session errors http_pool idle jobs media messages routing'''.split()


def transport_platform(path):
    with Path(path).open('rb') as stream:
        header = stream.read(64)
        if len(header) >= 20 and header[:4] == b'\x7fELF' and header[4:6] == b'\x02\x01':
            arch = {62: 'amd64', 183: 'arm64'}.get(struct.unpack_from('<H', header, 18)[0])
            if arch:
                return 'linux-' + arch
        if len(header) >= 8 and header[:4] == b'\xcf\xfa\xed\xfe':
            arch = {0x1000007: 'amd64', 0x100000c: 'arm64'}.get(struct.unpack_from('<I', header, 4)[0])
            if arch:
                return 'darwin-' + arch
        if len(header) == 64 and header[:2] == b'MZ':
            offset = struct.unpack_from('<I', header, 60)[0]
            if 64 <= offset <= 1024 * 1024:
                stream.seek(offset)
                pe = stream.read(6)
                if len(pe) == 6 and pe[:4] == b'PE\0\0':
                    arch = {0x8664: 'amd64', 0xaa64: 'arm64'}.get(struct.unpack_from('<H', pe, 4)[0])
                    if arch:
                        return 'windows-' + arch
    raise ValueError('Unsupported Fleet transport executable format')


def bundle_client(vendor, *, platform, transport=None):
    """Copy canonical client modules into a package-owned pantheon directory."""
    vendor = Path(vendor)
    if transport is not None:
        transport = Path(transport)
        if transport.is_symlink() or not transport.is_file() or transport_platform(transport) != platform:
            raise ValueError('Supply a regular target-platform Fleet workload transport')
    source = Path(__file__).parents[1]
    modules = [f'models/{name}.py' for name in CLIENT_MODULES] + [
        'apps/runtime_config.py', 'apps/dependency_client.py', 'apps/dependency_binding_client.py',
        'utils/adapters/image_blocks.py']
    for name in modules:
        target = vendor / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    # Keep existing package initializers intact when augmenting an App SDK.
    for directory in (vendor, vendor / 'models', vendor / 'apps', vendor / 'utils', vendor / 'utils/adapters'):
        init = directory / '__init__.py'
        if not init.exists():
            init.write_text('')
    if transport is not None:
        target = vendor / 'models' / ('fleet-app-transport.exe' if platform.startswith('windows-') else 'fleet-app-transport')
        shutil.copyfile(transport, target)
        target.chmod(0o755)
