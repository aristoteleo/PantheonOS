"""Main comparison on the single-cell tasks (molt_biology.md, step 2): Molt (frozen at b011425b) vs
SimpleTES / OpenEvolve / ShinkaEvolve / AIDE on bio_integration and bio_denoise, seeds 0-4,
400 evaluations, $4 cap, deepseek-v4.1-flash reasoning off -- the AHC protocol (make_main.py), every
method starting from the task's own start program. Eval servers read the train datasets only.
Eval servers (8 CPUs, one evaluation at a time): every bio_integration run has its own (an
evaluation takes 100-220 s there, pilot 2026-11-12: 400 of them are 11-25 h); on bio_denoise
(10-130 s) each seed has three, shared as in GROUPS. Sharing only changes how long a run waits.
Writes specs/bio_main.json and prints the servers (name port task)."""
import json
from pathlib import Path

EVO = Path("/scratch/users/wzxu/evolve")
SPECS = EVO / "PantheonOS-hoff/examples/evolution_bench/sherlock/specs"
MODEL = "deepseek/deepseek-v4.1-flash"
EVALS, USD, PORT0 = 400, 4.0, 8300
GROUPS = {"bio_integration": (("molt",), ("simpletes",), ("openevolve",), ("shinka",), ("aide",)),
          "bio_denoise": (("molt", "simpletes"), ("openevolve", "shinka"), ("aide",))}
SHORT = {"bio_integration": "bint", "bio_denoise": "bden"}


def bench(task, method, s, server):
    return {"out": f"biomain/{task}_{method}_s{s}", "eval_server_file": str(EVO / f"eval_server_{server}.url"), "argv": [
        "--task", task, "--method", method, "--seed", str(s), "--workers", "2",
        "--max-eval-calls", str(EVALS), "--max-llm-calls", str(5 * EVALS), "--max-llm-usd", str(USD),
        "--iterations", str(5 * EVALS), "--max-inner-evals", "4",
        "--model", "openrouter/" + MODEL, "--max-output-tokens", "32768", "--reply-retries", "3", "--reasoning-off"]}


def upstream(task, opt, s, server):
    return {"out": f"biomain/{task}_{opt}_s{s}", "venv": opt, "eval_server_file": str(EVO / f"eval_server_{server}.url"), "argv": [
        "--optimizer", opt, "--task", task, "--evals", str(EVALS),
        "--seed", str(s), "--model", MODEL, "--max-llm-calls", str(5 * EVALS), "--max-llm-usd", str(USD),
        "--wall-hours", "44"]}


rows, servers = [], {}
for task, groups in GROUPS.items():
    for s in range(5):
        for g, methods in enumerate(groups):
            server = f"{SHORT[task]}{s}{'abcde'[g]}"
            servers[server] = (PORT0 + len(servers), task)
            for m in methods:
                rows.append((bench if m in ("molt", "simpletes") else upstream)(task, m, s, server))
(SPECS / "bio_main.json").write_text(json.dumps(rows, indent=1))
print(len(rows), "rows")
for name, (port, task) in servers.items():
    print(name, port, task)
