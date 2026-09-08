"""Explicit isolated GUI acceptance runner for image guards and queue expiry.

Run manually in a disposable Linux fixture with QuPath and Xvfb installed.
Owns one previously unused DISPLAY and a new scratch directory; never uses
user images or an existing GUI. No tests run during ordinary pytest discovery.
"""

import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time


def load_fixture(repo):
    spec = importlib.util.spec_from_file_location("qupath_live_fixture", repo / "tests/test_qupath_bridge_live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def wait_file(path, timeout=15):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"Timed out waiting for fixture marker: {path.name}")
        await asyncio.sleep(0.02)


async def final_status(bridge, request_id, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        result = bridge.request_status(request_id)
        if result["state"] not in {"queued", "running"}:
            return result
        assert time.monotonic() < deadline, result
        await asyncio.sleep(0.05)


async def validate(args, fixture, bridge):
    checks = {}
    deadline = time.monotonic() + 45
    while True:
        state = await fixture.complete(bridge, "state")
        assert state["state"] == "succeeded", state
        if state["result"]["image"]:
            break
        assert time.monotonic() < deadline, "Synthetic image did not open"
        await asyncio.sleep(0.1)
    token = state["result"]["image_token"]
    assert token and (await fixture.complete(bridge, "state"))["result"]["image_token"] == token
    checks["stable_image_identity"] = token

    roi_params = {"expected_image": token, "script": """
def annotation = PathObjects.createAnnotationObject(ROIs.createRectangleROI(20, 30, 40, 50, ImagePlane.getDefaultPlane()))
addObject(annotation)
return getAnnotationObjects().size()
"""}
    result = await fixture.complete(bridge, "script", roi_params, request_id="guard-roi")
    assert result["state"] == "succeeded" and result["result"] == 1, result
    assert await fixture.complete(bridge, "script", roi_params, request_id="guard-roi") == result
    state = await fixture.complete(bridge, "state")
    assert state["result"]["objects"]["annotation_count"] == 1, state
    checks["guarded_roi_and_idempotent_retry"] = result

    mismatch_marker = args.directory / "mismatched-mutation.txt"
    result = await fixture.complete(bridge, "script", {"script": "new File(args[0]).text = 'wrong'; return true",
        "args": [str(mismatch_marker)], "expected_image": None})
    assert result["state"] == "failed" and "current image changed" in result["error"], result
    assert not mismatch_marker.exists()
    checks["explicit_null_guard_rejects_existing_image"] = result

    # Occupy the bridge's serial worker before publishing a mutation for A.
    # The worker then installs a new ImageData for the same synthetic server,
    # proving file paths alone would not distinguish the two in-memory images.
    started, release = args.directory / "worker.started", args.directory / "worker.release"
    blocker = await bridge.call("script", {"expected_image": token, "args": [str(started), str(release)], "script": """
new File(args[0]).text = 'started'
long deadline = System.currentTimeMillis() + 15000
while (!new File(args[1]).exists()) {
    if (System.currentTimeMillis() > deadline) throw new IllegalStateException('fixture release timed out')
    Thread.sleep(20)
}
def previous = getCurrentImageData()
def task = new java.util.concurrent.FutureTask({
    previous.setChanged(false)
    def nextImage = new qupath.lib.images.ImageData(previous.getServer(), previous.getImageType())
    getQuPath().getViewer().setImageData(nextImage)
    return true
} as java.util.concurrent.Callable)
javafx.application.Platform.runLater(task)
return task.get()
"""}, request_id="worker-blocker", timeout=0)
    await wait_file(started)
    pending_marker = args.directory / "queued-mutation.txt"
    pending = await bridge.call("script", {"script": "new File(args[0]).text = 'wrong'; return true",
        "args": [str(pending_marker)], "expected_image": token}, request_id="queued-old-image", timeout=0)
    assert pending["state"] in {"queued", "running"}, pending
    release.touch()
    blocker_result = await final_status(bridge, blocker["request_id"])
    assert blocker_result["state"] == "succeeded", blocker_result
    result = await final_status(bridge, pending["request_id"])
    assert result["state"] == "failed" and "current image changed" in result["error"], result
    assert not pending_marker.exists()
    state = await fixture.complete(bridge, "state")
    assert state["result"]["image_token"] != token
    assert state["result"]["objects"]["annotation_count"] == 0
    token = state["result"]["image_token"]
    checks["queued_worker_rejects_replaced_image_data"] = result

    # Queue an FX script behind a blocked FX callback that replaces the image.
    started, release = args.directory / "fx-switch.started", args.directory / "fx-switch.release"
    result = await fixture.complete(bridge, "script", {"expected_image": token,
        "args": [str(started), str(release)], "script": """
def previous = getCurrentImageData()
javafx.application.Platform.runLater {
    new File(args[0]).text = 'started'
    long deadline = System.currentTimeMillis() + 15000
    while (!new File(args[1]).exists()) {
        if (System.currentTimeMillis() > deadline) throw new IllegalStateException('fixture release timed out')
        Thread.sleep(20)
    }
    previous.setChanged(false)
    getQuPath().getViewer().setImageData(new qupath.lib.images.ImageData(previous.getServer(), previous.getImageType()))
}
return true
"""})
    assert result["state"] == "succeeded", result
    await wait_file(started)
    pending_marker = args.directory / "queued-fx-mutation.txt"
    pending = await bridge.call("script", {"script": "new File(args[0]).text = 'wrong'; return true",
        "args": [str(pending_marker)], "expected_image": token, "thread": "fx"},
        request_id="queued-fx-old-image", timeout=0)
    release.touch()
    result = await final_status(bridge, pending["request_id"])
    assert result["state"] == "failed" and "current image changed" in result["error"], result
    assert not pending_marker.exists()
    state = await fixture.complete(bridge, "state")
    assert state["result"]["image_token"] != token
    token = state["result"]["image_token"]
    checks["queued_fx_script_rejects_replaced_image_data"] = result

    for thread in ["worker", "fx"]:
        marker = args.directory / f"fx-block-{thread}.started"
        result = await fixture.complete(bridge, "script", {"expected_image": token, "args": [str(marker)], "script": """
javafx.application.Platform.runLater {
    new File(args[0]).text = 'started'
    Thread.sleep(2500)
}
return true
"""})
        assert result["state"] == "succeeded", result
        await wait_file(marker)
        output = args.directory / f"expired-{thread}.txt"
        pending = await bridge.call("script", {"script": "new File(args[0]).text = 'wrong'; return true",
            "args": [str(output)], "expected_image": token, "thread": thread},
            request_id=f"expire-on-fx-{thread}", timeout=0, queue_timeout=1)
        result = await final_status(bridge, pending["request_id"])
        assert result["state"] == "expired", result
        assert not output.exists()
        checks[f"{thread}_request_expires_while_waiting_for_fx"] = result

    result = await fixture.complete(bridge, "script", {"expected_image": token, "thread": "fx", "script": """
getQuPath().getImageData().setChanged(false)
getQuPath().getViewer().setImageData(null)
return true
"""})
    assert result["state"] == "succeeded", result
    state = await fixture.complete(bridge, "state")
    assert state["state"] == "succeeded", state
    assert state["result"]["image_token"] is None and state["result"]["image"] is None
    result = await fixture.complete(bridge, "script", {"expected_image": None, "script": "return 'no image expected'"})
    assert result["state"] == "succeeded", result
    checks["explicit_null_guard_matches_empty_viewer"] = result
    report = {"passed": True, "checks": checks}
    fixture.write_json(args.directory / "acceptance.json", report)
    print(json.dumps({"event": "guard_acceptance", "passed": True, "checks": list(checks)}), flush=True)


async def cleanup(args, fixture, bridge, xvfb):
    qupath_exited = True
    runtime_path = args.directory / "runtime.json"
    if runtime_path.exists():
        runtime = json.loads(runtime_path.read_text())
        pid = runtime["pid"]
        if bridge:
            await bridge.call("script", {"thread": "fx", "script": """
def gui = getQuPath()
gui.getImageData()?.setChanged(false)
new ArrayList(javafx.stage.Window.getWindows()).findAll { it != gui.getStage() }.each { it.hide() }
// Allow any dismissed modal's nested event loop to unwind first.
javafx.application.Platform.runLater { gui.sendQuitRequest() }
return true
"""}, timeout=0)
        deadline = time.monotonic() + 20
        qupath_exited = False
        while time.monotonic() < deadline:
            try:
                # Reap our launcher so a zombie cannot appear to be live.
                waited, _ = os.waitpid(pid, os.WNOHANG)
                if waited == pid:
                    qupath_exited = True
                    break
            except ChildProcessError:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    qupath_exited = True
                    break
            await asyncio.sleep(0.1)
    if qupath_exited:
        xvfb.terminate()
        xvfb.wait(timeout=10)
    print(json.dumps({"event": "guard_cleanup", "qupath_exited_normally": qupath_exited,
                      "owned_xvfb_stopped": xvfb.poll() is not None}), flush=True)
    if not qupath_exited:
        raise RuntimeError("Fixture QuPath did not close normally; owned Xvfb retained for diagnosis")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--display", default=":98")
    parser.add_argument("--executable", default="/opt/qupath/bin/QuPath")
    args = parser.parse_args()
    number = args.display.removeprefix(":")
    if not number.isdecimal() or Path(f"/tmp/.X11-unix/X{number}").exists():
        raise RuntimeError("Use a previously unused dedicated DISPLAY")
    if args.directory.exists():
        raise RuntimeError("Use a new scratch directory")
    fixture = load_fixture(args.repo)
    xvfb = subprocess.Popen(["Xvfb", args.display, "-screen", "0", "1440x1000x24", "-nolisten", "tcp"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    bridge = None
    try:
        await wait_file(Path(f"/tmp/.X11-unix/X{number}"))
        bridge = await fixture.start(args)
        await validate(args, fixture, bridge)
    finally:
        await cleanup(args, fixture, bridge, xvfb)


if __name__ == "__main__":
    asyncio.run(main())
