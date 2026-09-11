"""Apps distributed through Store, rather than installed with the OS image.

Keep this list independent of optional source checkouts. Old images may still
contain these directories; their presence is not an installation decision.
"""
STORE_APPS = frozenset({
    'cytoscape', 'gosling', 'igv', 'imagej', 'molstar', 'msa', 'phylotree',
    'qupath', 'rdkit', 'spatial3d', 'vitessce', 'viv', 'volume3d',
})

SYSTEM_APPS = frozenset({
    'agent', 'agent-view', 'browser', 'desktop', 'evolution', 'file-manager',
    'file-transfer', 'files', 'fleet', 'image-generation', 'image-viewer',
    'interfaces', 'mcp-gateway', 'node-files', 'integrated-notebook', 'pdf-viewer',
    'pty', 'python-interpreter', 'scraper', 'settings', 'shell', 'store', 'task',
    'terminal', 'text-viewer', 'web',
})


def included(manifest: dict, scope: str) -> bool:
    return scope != 'builtin' or manifest.get('id') not in STORE_APPS
