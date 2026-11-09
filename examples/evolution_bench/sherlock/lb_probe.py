"""Score one program through an eval URL. python3 lb_probe.py URL TASK SOLUTION.cpp  (needs OPENROUTER_API_KEY)"""
import hashlib, json, os, sys, time, urllib.request

url, task, path = sys.argv[1:4]
tok = hashlib.sha256(f"evolve-eval:{os.environ.get('OPENROUTER_API_KEY', '')}".encode()).hexdigest()[:32]
data = json.dumps({"token": tok, "task": task, "files": {"solution.cpp": open(path).read()}, "fidelity": "full"}).encode()
t0 = time.time()
req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
body = json.load(urllib.request.urlopen(req, timeout=1200))
print(json.dumps({"secs": round(time.time() - t0, 1), "success": body.get("success"), "error": body.get("error"),
                  "score": (body.get("metrics") or {}).get("combined_score"), "boot_id": body.get("boot_id")}))
