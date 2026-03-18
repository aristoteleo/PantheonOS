# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

from PyInstaller.utils.hooks import copy_metadata, collect_data_files
import os, sys, shutil

# nats-server-bin package installs the binary to .venv/bin/ (or Scripts/ on Windows).
# PyInstaller doesn't bundle it automatically, so we find and include it explicitly.
_nats_bin = shutil.which('nats-server.exe' if sys.platform == 'win32' else 'nats-server')
if not _nats_bin:
    raise FileNotFoundError("nats-server binary not found. Ensure nats-server-bin is installed.")

datas = [
    ('pantheon/factory/templates', 'pantheon/factory/templates'),
    ('pantheon/toolsets/database_api/schemas', 'pantheon/toolsets/database_api/schemas'),
    ('pantheon/chatroom/nats-ws.conf', 'pantheon/chatroom'),
    ('pantheon/toolsets/knowledge/config.yaml', 'pantheon/toolsets/knowledge'),
]
datas += copy_metadata('fastmcp')
datas += collect_data_files('litellm', includes=['**/*.json'])
datas += collect_data_files('tiktoken_ext', includes=['**/*.py'])
# fakeredis: model/_command_info.py loads os.path.join(dirname(__file__), '..', 'commands.json')
# PyInstaller must include the JSON so the relative path resolves at runtime.
# Also include model/__init__.py so the model/ directory exists in the bundle
# (avoids runtime os.makedirs in read-only install locations like Program Files).
datas += collect_data_files('fakeredis', includes=['commands.json'])
import fakeredis
_fakeredis_dir = os.path.dirname(fakeredis.__file__)
_model_init = os.path.join(_fakeredis_dir, 'model', '__init__.py')
if os.path.exists(_model_init):
    datas.append((_model_init, 'fakeredis/model'))

a = Analysis(
    ['pantheon/chatroom/__main__.py'],
    pathex=[],
    binaries=[(_nats_bin, '.')],  # nats-server binary from nats-server-bin package
    datas=datas,
    hiddenimports=[
        'pantheon',
        'pantheon.chatroom',
        'pantheon.endpoint',
        'pantheon.remote',
        'pantheon.toolsets',
        # All toolsets (dynamically imported via importlib)
        'pantheon.toolsets.code',
        'pantheon.toolsets.shell',
        'pantheon.toolsets.python',
        'pantheon.toolsets.file',
        'pantheon.toolsets.file_transfer',
        'pantheon.toolsets.notebook',
        'pantheon.toolsets.image',
        'pantheon.toolsets.database_api',
        'pantheon.toolsets.browser_use',
        'pantheon.toolsets.evolution',
        'pantheon.toolsets.task',
        'pantheon.toolsets.rag',
        'pantheon.toolsets.scfm',
        'nats',
        'litellm',
        'openai',
        'anthropic',
        'fastmcp',
        'fastmcp.server',
        'fastmcp.client',
        'lupa', 'lupa.lua51',
        'tiktoken',
        'tiktoken_ext',
        'tiktoken_ext.openai_public',
        'importlib.metadata',
        'importlib_metadata',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['runtime_hook_tiktoken.py'],
    excludes=[
        # ── Knowledge / vector DB (lancedb only used in RAG toolset, lazy import) ──
        'lancedb', 'lance', 'pyarrow', 'llama_index', 'qdrant_client',
        # ── Heavy transitive deps not directly imported ──
        'scipy', 'scipy.libs',           # ~140MB, only SCFM toolset (lazy)
        'patchright',                     # ~133MB, crawl4ai transitive
        'networkx',                       # ~18MB, transitive
        'fontTools',                      # ~27MB, matplotlib transitive
        'shapely', 'shapely.libs',        # ~12MB, transitive
        'alphashape',                     # transitive
        'nltk', 'nltk_data',             # ~13MB, transitive
        # ── Dev / build tools ──
        'debugpy',                        # ~23MB, dev only
        'pytest', 'pytest_asyncio',
        'pip', 'setuptools', 'pkg_resources',
        'PyInstaller', '_pyinstaller_hooks_contrib',
        # ── Unused heavy modules ──
        'tkinter', '_tkinter',
        'torch', 'tensorflow', 'sklearn', 'cv2',
        'IPython', 'ipywidgets',
        'sympy',
        'pandas',                         # ~45MB, not imported by backend code
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name='pantheon-backend-exe',
    debug=False,
    bootloader_ignore_signals=False,
    strip=sys.platform != 'win32',  # MinGW strip corrupts MSVC-built DLLs on Windows
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# COLLECT renames the exe back to 'pantheon-backend' inside the output directory
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=sys.platform != 'win32',
    upx=False,
    name='pantheon-backend',
)
