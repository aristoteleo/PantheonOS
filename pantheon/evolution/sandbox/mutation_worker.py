"""Runs ONE evolution mutation — executed INSIDE a Modal sandbox.

This file is uploaded to the sandbox as source and run with the image's pantheon; it is
NOT imported by the controller. All inputs arrive as base64 env vars (so the system prompt,
objective, evaluator, and parent code are injected at runtime, nothing baked into the image):

  EVO_PARENT     base64(json {relpath: content})   -- the code to improve
  EVO_EVALUATOR  base64(evaluator.py source)        -- defines evaluate(workspace_path)
  EVO_OBJECTIVE  base64(objective text)
  EVO_PROMPT     base64(agent system prompt)
  EVO_MODEL      model string (e.g. openrouter/z-ai/glm-5.2)
  EVO_TIMEOUT    seconds (default 600)
  + the LLM provider key (e.g. OPENROUTER_API_KEY)

It builds the single coding agent (file/python/shell + run_evaluator + submit), runs it,
then evaluates the submitted child and prints one line to stdout:

  EVO_RESULT_JSON:{"submitted": ..., "summary": ..., "child_files": {...}, "metrics": {...}}

(pantheon logs go to stderr, so stdout carries only the result line.)
"""
import asyncio
import base64
import json
import os
import tempfile


def _dec(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return base64.b64decode(v).decode() if v else default


async def main():
    from pantheon.agent import Agent
    from pantheon.evolution.evaluator import HybridEvaluator
    from pantheon.evolution.program import CodebaseSnapshot, Program
    from pantheon.internal.compression.plugin import CompressionPlugin
    from pantheon.internal.memory import Memory
    from pantheon.team.pantheon import PantheonTeam
    from pantheon.toolsets.file import FileManagerToolSet
    from pantheon.toolsets.python import PythonInterpreterToolSet
    from pantheon.toolsets.shell import ShellToolSet

    parent_files = json.loads(_dec("EVO_PARENT", "{}"))
    evaluator_code = _dec("EVO_EVALUATOR")
    objective = _dec("EVO_OBJECTIVE")
    system_prompt = _dec("EVO_PROMPT")
    model = os.environ.get("EVO_MODEL", "normal")
    timeout = int(os.environ.get("EVO_TIMEOUT", "600"))

    # materialize the parent code into a scratch workspace (inside the sandbox)
    wt = tempfile.mkdtemp(prefix="evo_wt_")
    for path, content in parent_files.items():
        fp = os.path.join(wt, path)
        os.makedirs(os.path.dirname(fp) or wt, exist_ok=True)
        with open(fp, "w") as f:
            f.write(content)

    # Optional MAP-Elites inspirations: elites from OTHER niches, dropped into a read-only
    # reference folder so the agent can borrow/combine ideas across cells (not just its lineage).
    inspirations = json.loads(_dec("EVO_INSPIRATIONS")) if os.environ.get("EVO_INSPIRATIONS") else []
    insp_note = ""
    if inspirations:
        insp_dir = os.path.join(wt, "inspirations")
        os.makedirs(insp_dir, exist_ok=True)
        readme = ["# Inspirations: high-scoring solutions found in OTHER niches of the search.",
                  "# They use DIFFERENT structures/heuristics. Study them for ideas, then write your",
                  "# OWN improved algorithm (the no-solver rule still applies). Do NOT edit this folder.",
                  ""]
        for k, ins in enumerate(inspirations, 1):
            sub = os.path.join(insp_dir, f"insp_{k}")
            for path, content in (ins.get("files") or {}).items():
                fp = os.path.join(sub, path)
                os.makedirs(os.path.dirname(fp) or sub, exist_ok=True)
                with open(fp, "w") as f:
                    f.write(content)
            readme.append(f"- insp_{k}/  score={ins.get('score')}  "
                          f"{(ins.get('summary') or '').strip()[:100]}")
        with open(os.path.join(insp_dir, "README.txt"), "w") as f:
            f.write("\n".join(readme) + "\n")
        insp_note = (
            f"\n\nThe read-only `inspirations/` folder holds {len(inspirations)} alternative "
            "high-scoring solutions from OTHER niches of the search (see inspirations/README.txt for "
            "each one's score and approach). Study their DIFFERENT ideas, then borrow/combine them "
            "into your OWN improved algorithm. Do not edit that folder.")

    evaluator = HybridEvaluator(evaluator_code, function_weight=1.0, llm_weight=0.0,
                                timeout=min(timeout, 120),
                                workspace_base=tempfile.mkdtemp(prefix="evo_eval_"))
    submitted: dict = {}

    def _current_files() -> dict:
        out = {}
        for path, original in parent_files.items():
            fp = os.path.join(wt, path)
            out[path] = open(fp).read() if os.path.exists(fp) else original
        return out

    async def run_evaluator() -> dict:
        """Run the objective's evaluator on the CURRENT code in your working directory and
        return its metrics. Higher fitness is better. If the solution is INVALID it scores 0
        and 'feedback' explains WHY — fix that before submitting."""
        res = await evaluator.evaluate(
            Program(id="_probe", snapshot=CodebaseSnapshot(files=_current_files()), generation=0))
        out = {"success": res.success, "metrics": res.metrics, "error": res.error}
        fb = {k: v for k, v in (res.artifacts or {}).items()
              if k not in ("llm_feedback", "issues", "suggestions") and v}
        if fb:
            out["feedback"] = fb
        return out

    async def submit(summary: str) -> str:
        """Commit your best version together with a short summary — like a git commit.
        Call this exactly once, when done."""
        submitted["files"] = _current_files()
        submitted["summary"] = (summary or "").strip()
        return "Submitted. Your result and summary are recorded."

    def think(thought: str) -> str:
        """Use this tool to think through the problem step by step."""
        return "Thought recorded."

    agent = Agent(name="code-evolver", instructions=system_prompt, model=model,
                  tools=[think, run_evaluator, submit], use_memory=True)
    await agent.toolset(FileManagerToolSet("evo-fm", wt))
    await agent.toolset(PythonInterpreterToolSet(name="evo-py", workdir=wt))
    await agent.toolset(ShellToolSet("evo-sh", workdir=wt))
    team = PantheonTeam(agents=[agent], plugins=[CompressionPlugin(
        {"enable": True, "threshold": 0.8, "preserve_recent_messages": 5})])

    prompt = (f"Objective: {objective}\n\n"
              "The code to improve is at the ROOT of your working directory. Improve it, verify with "
              "run_evaluator (invalid solutions score 0 — read the feedback). You have LIMITED time: "
              "keep a valid improvement saved as you go, and call submit(summary=...) with your best "
              "VALID version before you run out — a modest valid gain beats submitting nothing."
              + insp_note)
    error = ""
    try:
        await asyncio.wait_for(team.run(prompt, memory=Memory(name="evo-mut")), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"[:300]

    # Salvage: if the agent ran out of time without an explicit submit but left a VALID improvement
    # on disk, auto-submit it (a valid gain beats returning nothing).
    if not submitted:
        try:
            from pantheon.evolution.utils.metrics import compute_fitness_score
            cur = _current_files()
            seed_m = (await evaluator.evaluate(Program(
                id="_seed", snapshot=CodebaseSnapshot(files=parent_files), generation=0))).metrics
            cur_m = (await evaluator.evaluate(Program(
                id="_cur", snapshot=CodebaseSnapshot(files=cur), generation=0))).metrics
            if (compute_fitness_score(cur_m, [], None, 1.0, 0.0)
                    > compute_fitness_score(seed_m, [], None, 1.0, 0.0) + 1e-9):
                submitted["files"] = cur
                submitted["summary"] = "(auto-submitted best valid version on disk — ran out of time)"
        except Exception:
            pass

    result = {"submitted": bool(submitted), "summary": submitted.get("summary", ""),
              "child_files": submitted.get("files", {}), "error": error, "metrics": {}}
    if submitted:
        res = await evaluator.evaluate(
            Program(id="child", snapshot=CodebaseSnapshot(files=submitted["files"]), generation=1))
        # keep numeric metrics + fitness_weights + any string diagnostics (all JSON-safe); the
        # controller needs fitness_weights to compute QD fitness without re-running code on the host
        result["metrics"] = {k: v for k, v in res.metrics.items()
                             if isinstance(v, (int, float, str, dict, list))}
    print("EVO_RESULT_JSON:" + json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
