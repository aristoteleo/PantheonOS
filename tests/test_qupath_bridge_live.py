"""Explicit isolated-sandbox acceptance runner, never part of pytest discovery.

Run with --start-only on an existing Xpra DISPLAY, then --validate to reuse
that same GUI. It intentionally leaves the scratch GUI alive for the parent
acceptance run; that run owns sandbox termination. Private runtime.json must
not be printed: it contains the IPC token. No Hub or user volume is involved.
"""

import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import time
import zlib


def bridge_class(repo):
    spec = importlib.util.spec_from_file_location("qupath_bridge_live", repo / "apps/qupath/bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.QuPathBridge


def write_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2)


def synthetic_png(path):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    width, height = 1024, 768
    pixels = b"".join(b"\x00" + bytes(
        channel for x in range(width)
        for channel in (220 + (x // 32) % 30, 180 + (y // 16) % 60, 180 + (x // 64 + y // 64) % 60)
    ) for y in range(height))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


async def start(args):
    directory = args.directory
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    image_path = directory / "synthetic.png"
    synthetic_png(image_path)
    bridge = bridge_class(args.repo)(directory / "ipc", "native-bridge-live")
    env = dict(os.environ)
    env.update(bridge.launch_environment())
    env["DISPLAY"] = args.display
    env["JAVA_TOOL_OPTIONS"] = " ".join([
        bridge.startup_option(), '-Dprism.order=sw', '-Xmx2g',
        f'-Djava.util.prefs.userRoot="{directory / "prefs"}"',
        f'-Djava.io.tmpdir="{directory}"', '-Dqupath.win.width=1200', '-Dqupath.win.height=800',
    ])
    fd = os.open(directory / "qupath.log", os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "wb") as log:
        process = subprocess.Popen(
            [args.executable, '--quiet', f'--image={image_path}'], env=env, cwd=directory,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    write_json(directory / "runtime.json", {"pid": process.pid, "session_id": bridge.session_id,
               "bridge_dir": str(bridge.directory), "environment": bridge.launch_environment(),
               "image": str(image_path), "display": args.display})
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'QuPath exited: {process.returncode}; inspect private qupath.log')
        ready = bridge.ready()
        if ready:
            write_json(directory / "started.json", {"launcher_pid": process.pid, "ready": ready,
                       "display": args.display, "image": str(image_path)})
            print(json.dumps({"event": "ready", "launcher_pid": process.pid, **ready}))
            return bridge
        await asyncio.sleep(0.2)
    raise RuntimeError('QuPath startup bridge did not become ready; inspect private qupath.log')


def restore(args):
    runtime = json.loads((args.directory / "runtime.json").read_text())
    os.kill(runtime["pid"], 0)
    cls = bridge_class(args.repo)
    bridge = cls.__new__(cls)
    bridge.directory = Path(runtime["bridge_dir"])
    bridge.session_id = runtime["session_id"]
    bridge._token = runtime["environment"]["PANTHEON_QUPATH_BRIDGE_TOKEN"]
    return bridge


async def complete(bridge, method, params=None, request_id=None):
    result = await bridge.call(method, params, request_id=request_id, timeout=2)
    deadline = time.monotonic() + 60
    while result['state'] in {'running', 'queued'} and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        result = bridge.request_status(result['request_id'])
    return result


async def validate(args, bridge):
    run_id = str(time.time_ns())
    report = {'ready': bridge.ready(), 'checks': {}}
    checks = report['checks']
    deadline = time.monotonic() + 45
    while True:
        current = await complete(bridge, 'state')
        assert current['state'] == 'succeeded', current
        if current['result']['image']:
            break
        assert time.monotonic() < deadline, 'Initial image never opened'
        await asyncio.sleep(0.5)
    checks['state'] = current
    assert current['result']['image']['width'] == 1024
    initial_annotations = current['result']['objects']['annotation_count']

    for thread in ['worker', 'fx']:
        result = await complete(bridge, 'script', {'thread': thread, 'args': ['bound'], 'script':
            "println('captured hello'); return [fx: javafx.application.Platform.isFxApplicationThread(), "
            "width: getCurrentImageData().getServer().getWidth(), arg: args[0], pid: ProcessHandle.current().pid()]"})
        assert result['state'] == 'succeeded', result
        assert result['result']['fx'] == (thread == 'fx')
        assert result['result']['pid'] == bridge.ready()['pid']
        assert result['result']['width'] == 1024 and result['result']['arg'] == 'bound'
        assert 'captured hello' in result['stdout']
        checks[thread + '_thread_and_live_context'] = result

    references = (args.repo / 'apps/qupath/references/scripting.md').read_text()
    examples = re.findall(r'```groovy\n(.*?)\n```', references, re.S)
    assert len(examples) >= 3
    result = await complete(bridge, 'script', {'script': examples[0]})
    assert result['state'] == 'succeeded', result
    checks['documented_inspect_example'] = result
    roi_params = {'script': examples[1], 'args': ['100', '120', '160', '80', '0', '0', 'Agent ROI']}
    roi_request = 'create-roi-' + run_id
    result = await complete(bridge, 'script', roi_params, request_id=roi_request)
    assert result['state'] == 'succeeded', result
    roi_id = result['result']['id']
    assert result['result']['annotationCount'] == initial_annotations + 1
    assert await complete(bridge, 'script', roi_params, request_id=roi_request) == result
    checks['documented_roi_example'] = result
    current = await complete(bridge, 'state')
    assert current['result']['objects']['annotation_count'] == initial_annotations + 1
    assert any(obj['id'] == roi_id for obj in current['result']['objects']['annotations'])
    assert current['result']['image']['changed'] is True
    checks['live_annotation_and_dirty_state'] = current

    result = await complete(bridge, 'script', {'script': examples[2], 'args': [
        str(args.directory / f'annotations-{run_id}.geojson'), str(args.directory / f'measurements-{run_id}.tsv')]})
    assert result['state'] == 'succeeded', result
    checks['documented_export_example'] = result
    features = json.loads((args.directory / f'annotations-{run_id}.geojson').read_text())['features']
    assert len(features) == initial_annotations + 1

    save_path = str(args.directory / 'live-image.qpdata')
    result = await complete(bridge, 'script', {'script': 'qupath.lib.io.PathIO.writeImageData(new File(args[0]), getCurrentImageData()); return new File(args[0]).length()',
                                            'args': [save_path]})
    assert result['state'] == 'succeeded' and result['result'] > 0, result
    checks['save_live_image_data'] = result

    count_key = 'pantheon.live.count.' + run_id
    delayed = {'script': "Thread.sleep(1200); def n = Integer.parseInt(System.getProperty(args[0], '0')) + 1; "
                        "System.setProperty(args[0], n.toString()); return n", 'args': [count_key]}
    delayed_id = 'delayed-' + run_id
    pending = await bridge.call('script', delayed, request_id=delayed_id, timeout=0.05)
    assert pending.get('wait_timed_out') and pending['state'] in {'queued', 'running'}, pending
    result = await complete(bridge, 'script', delayed, request_id=delayed_id)
    assert result['state'] == 'succeeded' and result['result'] == 1, result
    assert await complete(bridge, 'script', delayed, request_id=delayed_id) == result
    checks['timeout_and_idempotency'] = {'initial': pending, 'final': result}

    result = await complete(bridge, 'script', {'script': "println('before failure'); throw new IllegalStateException('intentional acceptance failure')"})
    assert result['state'] == 'failed' and 'intentional acceptance failure' in result['error'], result
    assert 'before failure' in result['stdout']
    checks['error_capture'] = result
    result = await complete(bridge, 'script', {'script': 'return getCurrentImageData()'})
    assert result['state'] == 'failed' and 'Return JSON values' in result['error'], result
    checks['json_object_rejection'] = result
    result = await complete(bridge, 'script', {'script': "print('x' * 100000); return true"})
    assert result['state'] == 'succeeded' and result['output_truncated'] and len(result['stdout']) == 65536, result
    checks['output_bounds'] = {**result, 'stdout': f"<verified {len(result['stdout'])} characters>"}

    forged = {'session_id': bridge.session_id, 'request_id': 'invalid-auth', 'token': 'invalid',
              'method': 'script', 'params': {'script': 'throw new Error("must not execute")'}, 'expires_at': time.time() + 60}
    write_json(args.directory / 'forged.tmp', forged)
    os.replace(args.directory / 'forged.tmp', bridge.directory / 'invalid-auth.request.json')
    for _ in range(100):
        result = bridge.request_status('invalid-auth')
        if result['state'] == 'failed':
            break
        await asyncio.sleep(0.05)
    assert result['state'] == 'failed' and 'credentials' in result['error'], result
    checks['authentication'] = result
    report['passed'] = True
    write_json(args.directory / 'acceptance.json', report)
    print(json.dumps({'event': 'acceptance', 'passed': True, 'checks': list(checks)}))


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--directory', type=Path, default=Path('/tmp/pantheon-desktop-control/qupath-live'))
    parser.add_argument('--display', default=os.environ.get('DISPLAY', ':97'))
    parser.add_argument('--executable', default='/opt/qupath/bin/QuPath')
    parser.add_argument('--start-only', action='store_true')
    parser.add_argument('--validate', action='store_true')
    args = parser.parse_args()
    bridge = restore(args) if args.validate else await start(args)
    if not args.start_only:
        await validate(args, bridge)


if __name__ == '__main__':
    asyncio.run(main())
