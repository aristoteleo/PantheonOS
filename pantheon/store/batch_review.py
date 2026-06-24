"""Batch skill reviewer: score store skills with the skill-review rubric and write
the results back as the `pantheon-reviewer` bot.

Dependency-free (stdlib only). Calls the Anthropic Messages API over raw HTTP with a
tool-forced schema (so output is always rubric-valid), computes overall/verdict
deterministically, and writes one review per skill.

Write path:
  --write api  (default) : POST to the hub API as the pantheon-reviewer bot. Works
                           against any hub (dev SQLite or prod Postgres) — this is the
                           path the scheduled/cron reviewer uses.
  --write db             : write straight into a local SQLite dev_store.db (fast, dev only).

Incremental:
  --changed-only : skip skills whose content is unchanged since their last review
                   (current content_hash == the reviewed_content_hash recorded in the
                   existing pantheon-reviewer evaluation). So a nightly run only re-scores
                   newly-published or version-bumped skills.

Env:
  ANTHROPIC_API_KEY            required (the reviewer LLM key)
  REVIEW_MODEL                 default claude-sonnet-4-6
  PANTHEON_HUB_URL             default http://localhost:8000
  PANTHEON_REVIEWER_USER/PASS  bot creds for --write api (default pantheon-reviewer / env pass)
  DEV_STORE_DB                 sqlite path for --write db
"""
import argparse, json, os, sqlite3, sys, time, uuid, datetime, urllib.parse
import urllib.request as U
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from pantheon.store.reviewer import (
    REVIEW_SCHEMA, REVIEWER_SYSTEM, RUBRIC_VERSION, build_review_prompt, compute_overall,
)

HUB = os.environ.get("PANTHEON_HUB_URL", "http://localhost:8000").rstrip("/")
DB = os.environ.get("DEV_STORE_DB", os.path.expanduser("~/Projects/pantheon-hub/dev_store.db"))
REVIEWER_USER_ID = "pantheon_reviewer"
REVIEWER_USERNAME = os.environ.get("PANTHEON_REVIEWER_USER", "pantheon-reviewer")
REVIEWER_PASSWORD = os.environ.get("PANTHEON_REVIEWER_PASSWORD", "reviewer-bot-dev")
MODEL = os.environ.get("REVIEW_MODEL", "claude-sonnet-4-6")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def _get(path):
    with U.urlopen(HUB + path, timeout=30) as r:
        return json.loads(r.read())


def _post(path, body, token=None, form=False):
    if form:
        data = urllib.parse.urlencode(body).encode()
        ct = "application/x-www-form-urlencoded"
    else:
        data = json.dumps(body).encode()
        ct = "application/json"
    req = U.Request(HUB + path, data=data, method="POST")
    req.add_header("content-type", ct)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with U.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def login_bot():
    d = _post("/api/auth/login", {"username": REVIEWER_USERNAME, "password": REVIEWER_PASSWORD}, form=True)
    tok = d.get("access_token")
    if not tok:
        raise SystemExit(f"bot login failed: {str(d)[:120]}")
    return tok


def list_source_skills(source):
    out, off = [], 0
    while True:
        src = "" if source == "all" else f"&source={urllib.parse.quote(source)}"
        d = _get(f"/api/store/packages?type=skill{src}&limit=100&offset={off}")
        out.extend(d["packages"])
        if len(d["packages"]) < 100:
            break
        off += 100
    return out


def existing_reviewed_hash(pkg_name):
    """The content_hash the pantheon-reviewer last scored this skill at, or None."""
    try:
        d = _get(f"/api/store/packages/{urllib.parse.quote(pkg_name)}/reviews")
    except Exception:
        return None
    for r in d.get("reviews", []):
        if r.get("username") == REVIEWER_USERNAME:
            return (r.get("evaluation") or {}).get("reviewed_content_hash")
    return None


def review_one(pkg):
    """Call the LLM for one skill; return (pkg, evaluation_dict) or (pkg, None, error)."""
    try:
        dl = _get(f"/api/store/packages/{urllib.parse.quote(pkg['name'])}/download")
    except Exception as e:
        return pkg, None, f"download failed: {e}"
    skill = {**pkg, "content": dl.get("content", ""), "files": dl.get("files") or {}}
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 2000,
        "system": REVIEWER_SYSTEM,
        "messages": [{"role": "user", "content": build_review_prompt(skill)}],
        "tools": [{"name": "submit_review",
                   "description": "Submit the structured skill review.",
                   "input_schema": REVIEW_SCHEMA}],
        "tool_choice": {"type": "tool", "name": "submit_review"},
    }).encode()
    req = U.Request("https://api.anthropic.com/v1/messages", data=body, method="POST")
    req.add_header("x-api-key", API_KEY)
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("content-type", "application/json")
    for attempt in range(4):
        try:
            with U.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read())
            review = next((c["input"] for c in resp.get("content", []) if c.get("type") == "tool_use"), None)
            if not review:
                return pkg, None, "no tool_use in response"
            overall, verdict = compute_overall(review.get("scores", {}))
            review["overall"] = overall
            review["verdict"] = verdict
            review["rubric_version"] = RUBRIC_VERSION
            review["model"] = MODEL
            review["current_version"] = dl.get("version")
            # Stamp the content fingerprint we reviewed, so --changed-only can later
            # tell whether this skill needs re-reviewing.
            review["reviewed_content_hash"] = pkg.get("content_hash")
            return pkg, review, None
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529) and attempt < 3:
                time.sleep(2 ** attempt * 3)
                continue
            return pkg, None, f"HTTP {e.code}: {e.read()[:200]}"
        except Exception as e:
            if attempt < 3:
                time.sleep(2 ** attempt * 3)
                continue
            return pkg, None, f"{type(e).__name__}: {e}"
    return pkg, None, "exhausted retries"


def _rating(evaluation):
    return max(1, round(evaluation["overall"] / 20))


def write_api(token, pkg, evaluation):
    """Post the review as the pantheon-reviewer bot via the hub API."""
    comment = (evaluation.get("summary") or "").strip() or None
    _post(f"/api/store/packages/{pkg['id']}/reviews",
          {"rating": _rating(evaluation), "comment": comment, "evaluation": evaluation},
          token=token)


def write_db(con, pkg, evaluation):
    """Write straight into SQLite (dev only) + recompute aggregates."""
    cur = con.cursor()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    comment = (evaluation.get("summary") or "").strip() or None
    pkg_id = pkg["id"]
    cur.execute("DELETE FROM store_package_reviews WHERE package_id=? AND user_id=?", (pkg_id, REVIEWER_USER_ID))
    cur.execute("""INSERT INTO store_package_reviews
        (id,package_id,user_id,rating,comment,version,evaluation,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), pkg_id, REVIEWER_USER_ID, _rating(evaluation), comment,
         evaluation.get("current_version"), json.dumps(evaluation), now, now))
    rows = cur.execute("SELECT rating FROM store_package_reviews WHERE package_id=?", (pkg_id,)).fetchall()
    cur.execute("UPDATE store_packages SET rating_sum=?, rating_count=? WHERE id=?",
                (sum(r[0] for r in rows), len(rows), pkg_id))
    con.commit()


def run(source="bioSkills", write="api", changed_only=False, limit=0, workers=6,
        force=False, only=None):
    """Review skills and write results. Returns (ok, failed). See module docstring."""
    if not API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY not set")

    skills = list_source_skills(source)
    if only:
        names = set(only.split(",")) if isinstance(only, str) else set(only)
        skills = [s for s in skills if s["name"] in names]

    # Decide which skills actually need reviewing.
    if not force:
        kept = []
        for s in skills:
            prev = existing_reviewed_hash(s["name"])
            if prev is None:
                kept.append(s)                                   # never reviewed
            elif changed_only and prev != s.get("content_hash"):
                kept.append(s)                                   # content changed since last review
            elif not changed_only:
                pass                                             # already reviewed, not changed-only -> skip
        dropped = len(skills) - len(kept)
        skills = kept
        if dropped:
            print(f"Skipping {dropped} already-reviewed/unchanged.")
    if limit:
        skills = skills[:limit]
    print(f"Reviewing {len(skills)} '{source}' skills -> write={write}, model={MODEL}, workers={workers}")
    if not skills:
        print("Nothing to review.")
        return 0, 0

    token = login_bot() if write == "api" else None
    con = sqlite3.connect(DB) if write == "db" else None
    ok, fail = 0, 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(review_one, p): p for p in skills}
        for i, fut in enumerate(as_completed(futs), 1):
            pkg, review, *err = fut.result()
            if review is None:
                fail += 1
                print(f"  [{i}/{len(skills)}] FAIL {pkg['name'][:40]}: {err[0] if err else '?'}")
                continue
            try:
                if write == "api":
                    write_api(token, pkg, review)
                else:
                    write_db(con, pkg, review)
                ok += 1
            except Exception as e:
                fail += 1
                print(f"  [{i}/{len(skills)}] WRITE-FAIL {pkg['name'][:34]}: {type(e).__name__}: {str(e)[:120]}")
                continue
            if i % 20 == 0 or i == len(skills):
                rate = i / max(1e-9, time.time() - t0)
                print(f"  [{i}/{len(skills)}] ok={ok} fail={fail} ({rate:.1f}/s) last: "
                      f"{pkg['name'][:34]} -> {review['overall']}/{review['verdict']}")
    if con:
        con.close()
    print(f"\nDone: {ok} reviewed, {fail} failed, {time.time()-t0:.0f}s")
    return ok, fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="bioSkills", help="source label, or 'all' for every skill")
    ap.add_argument("--write", choices=["api", "db"], default="api",
                    help="api: POST as the pantheon-reviewer bot (prod-portable). db: direct SQLite (dev).")
    ap.add_argument("--changed-only", action="store_true",
                    help="only review skills whose content changed since their last review")
    ap.add_argument("--only", default=None, help="comma-separated store names to restrict to")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true", help="re-review even if unchanged/already reviewed")
    args = ap.parse_args()
    run(source=args.source, write=args.write, changed_only=args.changed_only,
        limit=args.limit, workers=args.workers, force=args.force, only=args.only)


if __name__ == "__main__":
    main()
