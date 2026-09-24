"""Frozen deterministic group-package compiler, executed without engine imports."""
from copy import deepcopy
import io
import json
from pathlib import Path
import re
import sys
import tarfile

from group_model import descriptor
import sglang_group


RUNTIME_FILES = ('group_network.py', 'group_mesh.py', 'group_supervisor.py',
    'group_model.py', 'sglang_group.py', 'sglang_runtime.py', 'snapshots.py',
    'sglang_group_runtime.py', 'group_connector.py', 'server.py', 'activity.py', 'idle.py', 'group_inference.py')
SOURCE_FILES = (*RUNTIME_FILES, 'group_packager.py')


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def compile_rank(request, recipe, model):
    if not isinstance(request, dict) or set(request) != {'plan', 'topology', 'ca_sha256', 'rank', 'source_sha256'}:
        raise ValueError('Build only an exact original creation rank')
    plan, rank = request['plan'], request['rank']
    model = descriptor(model)
    members, _, _, topology = sglang_group._validate(plan, model)
    if (topology.document() != request['topology'] or type(rank) is not int or not 0 <= rank < len(members)
            or [m['rank'] for m in plan['members']] != list(range(len(members)))
            or any(m['generation'] < 2 for m in members)
            or any(not isinstance(request[key], str) or not re.fullmatch('[a-f0-9]{64}', request[key])
                   for key in ('ca_sha256', 'source_sha256'))):
        raise ValueError('Package identity differs from canonical creation intent')
    if (not isinstance(recipe, dict) or recipe.get('id') != plan['recipe_id']
            or recipe.get('version') != sglang_group.sglang_runtime.VERSION
            or recipe.get('engine') != 'sglang' or recipe.get('runtime') != 'container'
            or recipe.get('platforms') != ['linux-amd64']
            or not re.fullmatch(r'[a-zA-Z0-9./_-]+@sha256:[a-f0-9]{64}', recipe.get('image', ''))):
        raise ValueError('Use the original digest-pinned Linux SGLang container recipe')
    # Compile every rank before any artifact becomes available, including each
    # node-local host-memory, GPU capacity and common static-memory fraction.
    for member in members:
        sglang_group.rank_launch(plan, model, member['rank'], member['physical_gpu_bytes'])
    status_port = next(port for port in range(30001, 30033)
                       if port not in {plan['rendezvous_port'], *(m['control_port'] for m in members)})
    definition = dict(protocol=1, app_id='model-service', version='0.1.0',
        # Existing bridge-only nodes must not install this distributed package.
        # The future admission driver owns the private namespace/interface; an
        # arbitrary host-network flag is deliberately not supplied by this App.
        requires=dict(os=['linux'], arch=['amd64'], caps=['proc', 'model-group-private-network']),
        dependencies=dict(container_engine=dict(provider='docker', provision='never')),
        components=[dict(name='backend', runtime='container', image=recipe['image'],
            argv=['python3', '/fleet/package/sglang_group_runtime.py', 'start'],
            group_peer=True, group_network=True, run_as_owner=True, ports={'http': status_port},
            mounts={'state': '/fleet/state'}, read_only_mounts={
                'package': '/fleet/package', 'cache/snapshots/' + model['sha256']: '/fleet/weights'},
            stop_seconds=30, resources=deepcopy(members[rank]['resources']),
            readiness=dict(argv=['python3', '/fleet/package/sglang_group_runtime.py', 'ready'], timeout_seconds=480))])
    return {'fleet.json': definition,
        'app.json': dict(id='model-service', name='Managed SGLang group rank', version='0.1.0',
            apiVersion=2, entry={}, execution=dict(protocol=1, manifest='fleet.json')),
        'group-peer.json': dict(protocol=1, rank=rank, topology=topology.document(), ca_sha256=request['ca_sha256']),
        'group-plan.json': plan, 'group-model.json': model,
        'group-build.json': dict(protocol=1, source_sha256=request['source_sha256'], rank=rank)}


def main():
    root = Path(__file__).parent
    request = json.loads((root / 'build-request.json').read_bytes())
    metadata = json.loads((root / 'source-metadata.json').read_bytes())
    generated = compile_rank(request, metadata['recipe'], metadata['model'])
    files = {name: (root / name).read_bytes() for name in RUNTIME_FILES}
    files.update({name: encode(value) for name, value in generated.items()})
    # Same canonical tar conventions as Fleet build_artifact, frozen with this
    # source. An Agent code update cannot silently change an existing rank hash.
    with tarfile.open(sys.argv[1], 'w') as archive:
        for name, data in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(data), 0o400
            archive.addfile(entry, io.BytesIO(data))


if __name__ == '__main__':
    main()
