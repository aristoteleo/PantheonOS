"""Build immutable HPC Apps and attach their owned, allocation-scoped services."""
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
from urllib.parse import urlsplit
import zipfile

from pantheon.apps.registry import BUILTIN_ROOT
from .hpc import cluster, service
from .inventory import node_inventory


def build(kind, name, *, python='python3', modules=None, atrium_origin='', engine='ollama',
          engine_argv=None, model_cache='$SCRATCH/pantheon-model-cache', model_name='', startup_seconds=300):
    if kind not in {'jupyterlab', 'model-service'}:
        raise ValueError('Choose JupyterLab or Model Services')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', name or ''):
        raise ValueError('Use a short lowercase App name')
    if not isinstance(python, str) or not python or len(python) > 1024 or '\0' in python:
        raise ValueError('Specify an installed Python executable')
    modules = modules or []
    if (not isinstance(modules, list) or len(modules) > 16
            or any(not isinstance(m, str) or not re.fullmatch(r'[A-Za-z0-9_./+-]{1,120}', m) for m in modules)):
        raise ValueError('Environment modules must be a list of module names')
    if type(startup_seconds) is not int or not 1 <= startup_seconds <= 600:
        raise ValueError('Startup limit must be 1–600 seconds')
    config = dict(kind=kind, startup_seconds=startup_seconds)
    names = []
    if kind == 'jupyterlab':
        origin = urlsplit(atrium_origin)
        if (origin.scheme not in {'https', 'http'} or not origin.hostname or origin.path or origin.query
                or origin.fragment or origin.username or origin.password
                or atrium_origin != origin.scheme + '://' + origin.netloc
                or any(ord(c) < 33 or ord(c) > 126 for c in atrium_origin)
                or (origin.scheme == 'http' and origin.hostname not in {'localhost', '127.0.0.1'})):
            raise ValueError('Provide the Atrium HTTPS origin (or localhost for development)')
        config['atrium_origin'] = atrium_origin
    else:
        if engine not in {'ollama', 'sglang'}:
            raise ValueError('HPC Model Services currently support Ollama and SGLang engines')
        if (not isinstance(engine_argv, list) or not 0 < len(engine_argv) <= 48
                or any(not isinstance(a, str) or '\0' in a or len(a) > 2048 for a in engine_argv)
                or not engine_argv[0]):
            raise ValueError('Specify the installed engine command as JSON arguments')
        if not isinstance(model_cache, str) or not 0 < len(model_cache) <= 1024 or '\0' in model_cache:
            raise ValueError('Specify a scratch model cache directory')
        if not isinstance(model_name, str) or len(model_name) > 200 or '\0' in model_name:
            raise ValueError('Invalid model name')
        config.update(engine=engine, engine_argv=engine_argv, model_cache=model_cache, model_name=model_name)
        names = ['server.py', 'activity.py', 'idle.py', 'media_http.py', 'jobs_http.py', 'credentials.py']
    # Include the exact existing connector implementation, not a second proxy.
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for file in names:
            z.writestr(file, (BUILTIN_ROOT / 'model-service' / file).read_bytes())
        z.writestr('hpc_app.py', Path(__file__).with_name('hpc_app_bootstrap.py').read_bytes())
    encoded = base64.b64encode(archive.getvalue()).decode()
    digest = hashlib.sha256(archive.getvalue()).hexdigest()
    bootstrap = '''import base64,hashlib,io,json,os,pathlib,sys,zipfile
data=base64.b64decode(sys.argv[1]);assert hashlib.sha256(data).hexdigest()==sys.argv[2]
root=pathlib.Path(os.environ['PANTHEON_HPC_WORKSPACE'])/'.app';root.mkdir(mode=0o700,exist_ok=True)
with zipfile.ZipFile(io.BytesIO(data)) as z:
 assert sum(i.file_size for i in z.infolist())<524288
 for i in z.infolist():
  assert i.filename==pathlib.Path(i.filename).name and i.filename.endswith('.py')
  fd=os.open(root/i.filename,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
  with os.fdopen(fd,'wb') as f:f.write(z.read(i))
sys.path.insert(0,str(root));import hpc_app;hpc_app.main(json.loads(sys.argv[3]))'''
    argv = [python, '-c', bootstrap, encoded, digest, json.dumps(config)]
    if modules:
        # Fixed module wrapper: names are quoted and the App argv stays positional.
        argv = ['bash', '-lc', 'set -e; module purge; module load ' +
                ' '.join(shlex.quote(m) for m in modules) + '; exec "$@"', 'pantheon-hpc-app', *argv]
    spec = dict(kind=kind, name=name, argv=argv, cwd='.', startup_seconds=startup_seconds)
    if len(json.dumps(spec).encode()) > 65536:
        raise ValueError('App command is too large')
    return spec


async def metadata(resolver, binding, expected_kind):
    if binding.get('component') != 'service' or binding.get('port') != 'http' or binding.get('generation') != 1:
        raise ValueError('Use an exact HPC App service binding')
    await resolver._ensure_client()
    nodes = node_inventory(await resolver._list_nodes(max_age=2))['nodes']
    node = next((n for n in nodes if n['node_id'] == binding['node_id']), None)
    if not node or (node.get('delegation') or {}).get('state') != 'ready':
        raise ValueError('The HPC job is not available; inspect it in Fleet')
    records = (await service(resolver, binding['node_id'])).get('services', [])
    match = next((s for s in records if all(s.get(k) == binding.get(k)
                 for k in ('instance_id', 'revision', 'generation'))), None)
    if not match or not match.get('primary') or match.get('kind') != expected_kind or match['state'] != 'running':
        raise ValueError('The exact HPC App is not running')
    reply = await resolver._client.hpc_file(binding['node_id'], 'read', path='.hpc-app.json')
    if reply.get('error') or reply.get('eof') is False:
        raise RuntimeError('HPC App access metadata is unavailable')
    data = json.loads(base64.b64decode(reply['data']))
    if (data.get('revision') != binding['revision'] or data.get('kind') != expected_kind
            or data.get('job_id') != node['delegation']['job_id']):
        raise ValueError('HPC App access metadata does not match this job')
    return node, data


async def submit(resolver, node_id, cluster_id, request, app):
    if not isinstance(app, dict) or not isinstance(request, dict) or 'service' in request:
        raise ValueError('Specify App settings and Slurm resources separately')
    spec = build(**app)
    return await cluster(resolver, node_id, 'submit', cluster_id=cluster_id,
                         request={**request, 'name': spec['name'], 'service': spec})
