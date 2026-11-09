"""Eval load balancer: takes over a slow eval server's host:port and spreads its requests over a
pool of eval servers, one measurement at a time per backend (FIFO wait for the first free one).

    python eval_lb.py --listen 8215 --listen 8216 --backend-file URLFILE ... [--log FILE]

Binding retries until each port is free, so it can be started before the old server is stopped.
SIGUSR1: stop listening (the port is free for a successor at once), finish in-flight requests, exit.
"""
import argparse
import asyncio
import json
import signal
import socket
import sys
import time
import uuid

from aiohttp import ClientSession, ClientTimeout, web


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, action="append", required=True)
    ap.add_argument("--backend-file", action="append", required=True)
    ap.add_argument("--log", default=None)
    a = ap.parse_args()

    backends = [open(f).read().strip() for f in a.backend_file]
    free: asyncio.Queue = asyncio.Queue()
    for b in backends:
        free.put_nowait(b)
    boot_id = "lb-" + uuid.uuid4().hex[:9]
    state = {"served": 0, "inflight": 0, "per": {b: 0 for b in backends}}
    logf = open(a.log, "a") if a.log else None
    session = ClientSession(timeout=ClientTimeout(total=1200))

    def log(**kw):
        if logf:
            logf.write(json.dumps({"t": round(time.time(), 1), **kw}) + "\n")
            logf.flush()

    async def evaluate(request):
        payload = await request.json()
        port = request.transport.get_extra_info("sockname")[1]
        state["inflight"] += 1
        t0 = time.time()
        try:
            body, last = None, None
            for _ in range(2):
                b = await free.get()
                tw = time.time()
                try:
                    async with session.post(b, json=payload) as r:
                        r.raise_for_status()
                        body = await r.json()
                    state["per"][b] += 1
                    log(port=port, backend=b, wait=round(tw - t0, 1), secs=round(time.time() - tw, 1),
                        ok=body.get("success"), task=payload.get("task"),
                        score=(body.get("metrics") or {}).get("combined_score"),
                        server_seconds=body.get("server_seconds"))
                    break
                except Exception as e:  # noqa: BLE001
                    last = f"{type(e).__name__}: {str(e)[:200]}"
                    log(port=port, backend=b, error=last)
                finally:
                    free.put_nowait(b)
            if body is None:
                body = {"success": False, "metrics": {}, "error": f"eval balancer: {last}"}
            state["served"] += 1
            return web.json_response(body)
        finally:
            state["inflight"] -= 1

    async def health(request):
        return web.json_response({"boot_id": boot_id, "evals_served": state["served"],
                                  "inflight": state["inflight"], "per_backend": state["per"],
                                  "free": free.qsize(), "host": socket.gethostname(),
                                  "warmup": {"balancer": True}})

    app = web.Application(client_max_size=64 * 1024 ** 2)
    app.router.add_post("/evaluate", evaluate)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()

    stop = asyncio.Event()                # installed before binding: a stray USR1 must not kill a waiter
    asyncio.get_running_loop().add_signal_handler(signal.SIGUSR1, stop.set)
    sites = []
    for port in a.listen:
        while True:
            try:
                s = web.TCPSite(runner, "0.0.0.0", port, reuse_address=True)
                await s.start()
                sites.append(s)
                print(f"[{time.ctime()}] {boot_id} listening on {port} -> {backends}", flush=True)
                log(event="listen", port=port)
                break
            except OSError:
                await asyncio.sleep(0.2)

    await stop.wait()
    for s in sites:                      # free the ports first, then drain
        await s.stop()
    log(event="handoff", inflight=state["inflight"])
    print(f"[{time.ctime()}] handoff: ports closed, draining {state['inflight']}", flush=True)
    while state["inflight"] > 0:
        await asyncio.sleep(0.5)
    await session.close()
    print(f"[{time.ctime()}] drained, exit", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
