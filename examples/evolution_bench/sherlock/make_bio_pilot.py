"""Short pilot runs of molt_biology.md step 1: Molt and SimpleTES, seed 99, 100 evaluations, one
eval server per task (biop<int|den|lab>). Not part of any comparison. Writes specs/bio_pilot.json and
prints the servers (name port task)."""
import json
from pathlib import Path

EVO = Path("/scratch/users/wzxu/evolve")
SPECS = EVO / "PantheonOS-hoff/examples/evolution_bench/sherlock/specs"
MODEL = "deepseek/deepseek-v4.1-flash"
EVALS, USD = 100, 1.0
# bio_labelproj failed rules 3-4 on the reference reads (headroom 0.026, transfer rho 0.2): no runs
SERVERS = {"bio_integration": ("biopint", 8240), "bio_denoise": ("biopden", 8241)}

rows = []
for task, (server, _) in SERVERS.items():
    for m in ("molt", "simpletes"):
        rows.append({"out": f"biopilot/{task}_{m}_s99", "eval_server_file": str(EVO / f"eval_server_{server}.url"),
                     "argv": ["--task", task, "--method", m, "--seed", "99", "--workers", "2",
                              "--max-eval-calls", str(EVALS), "--max-llm-calls", str(5 * EVALS),
                              "--max-llm-usd", str(USD), "--iterations", str(5 * EVALS), "--max-inner-evals", "4",
                              "--model", "openrouter/" + MODEL, "--max-output-tokens", "32768",
                              "--reply-retries", "3", "--reasoning-off"]})
(SPECS / "bio_pilot.json").write_text(json.dumps(rows, indent=1))
print(len(rows), "rows")
for task, (name, port) in SERVERS.items():
    print(name, port, task)
