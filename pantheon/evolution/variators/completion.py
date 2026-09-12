"""Mutation as a single completion: one prompt in, k whole programs out.

This is a different operator from `AgentVariator`, not a cheaper configuration of it. The agent
gets a workspace, a shell, a python interpreter and `run_evaluator`, so it can try an edit, measure
it, and iterate before committing -- it arrives having already verified its own work. This one gets
a prompt and returns text. Nothing is executed while it writes, it cannot see a score, and whatever
it emits is measured once by the loop.

It exists because that is what SimpleTES does -- `completion(model, messages, n=k)`, then a code
block pulled out of each choice -- and running its selection policy on top of an agentic operator
would not be running SimpleTES. Comparing search policies only means something when the operator
underneath them is held fixed, and holding it fixed at "full coding agent" quietly changes what is
being compared.

`n=k` asks for one request with k completions, which is SimpleTES's cost profile -- the prompt is
paid for once. **Measured caveat: OpenRouter ignores `n` and returns a single choice**, so behind
that gateway the top-up below turns one batch into k sequential requests and the prompt is paid
for k times. The search behaves identically; only the cost does not, which matters when the point
of a comparison is cost per candidate.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional

from pantheon.utils.log import logger

from ..core.genome import CodeGenome
from ..core.method import EvolveContext
from ..core.work import Create, Produced

FENCE = re.compile(r"```(?:[a-zA-Z0-9_+-]*)\n(.*?)```", re.S)

DEFAULT_SYSTEM = (
    "You are an expert algorithm designer. You will be shown a program and an objective. "
    "Reply with ONE complete, runnable replacement for the program, in a single fenced code "
    "block. No commentary outside the block, no diffs, no ellipses -- the block is written to "
    "disk verbatim and run as-is, so it must be the whole file."
)


def extract_code(text: str) -> Optional[str]:
    """The last fenced block, or the whole reply if it is bare code.

    Last rather than first: models often restate the original before giving the revision, and
    taking the first block silently re-submits the parent -- which scores identically and looks
    like a mutation that achieved nothing.
    """
    blocks = FENCE.findall(text or "")
    if blocks:
        return blocks[-1].strip() + "\n"
    stripped = (text or "").strip()
    if stripped.startswith(("import ", "from ", "def ", "class ", "#", '"""')):
        return stripped + "\n"
    return None


class EvolveBlock:
    """Upstream SimpleTES's EVOLVE-BLOCK protocol (simpletes/utils/code_extract.py).

    A seed that carries `EVOLVE-BLOCK-START` / `EVOLVE-BLOCK-END` marker lines is not
    regenerated whole: the model produces only the code between the markers, and the final
    program is EXACT_PREFIX + evolved_block + EXACT_SUFFIX with both fixed parts kept
    verbatim. This is the load-bearing half of how upstream evolves large programs -- our port
    originally ran whole-file regeneration on a 43KB seed and produced a result that was about
    the port, not the algorithm.
    """

    def __init__(self, program: str):
        self.prefix = self.suffix = ""
        self.block = program
        self.has_markers = False
        lines = program.splitlines(keepends=True)
        start = end = -1
        for i, ln in enumerate(lines):
            if "EVOLVE-BLOCK-START" in ln and start < 0:
                start = i
            elif "EVOLVE-BLOCK-END" in ln:
                end = i
                break
        if start < 0 or end < 0 or end <= start:
            return
        self.prefix = "".join(lines[: start + 1]).rstrip("\n")
        self.suffix = "".join(lines[end:]).lstrip("\n")
        self.block = "".join(lines[start + 1:end])
        self.has_markers = True
        self.start_line = lines[start].rstrip("\r\n")
        self.end_line = lines[end].rstrip("\r\n")

    def merge(self, reply: str) -> Optional[str]:
        """Reconstruct the full program from a model reply.

        The evolved block is the text between the marker lines of the reply's code (fenced or
        bare); a reply that dropped the markers is treated as being the bare block, which keeps
        an otherwise-good completion usable.

        Two reply shapes used to produce programs that could not compile, and on AHC039 they
        were half of all evaluations: a reply that carries the WHOLE file (with or without the
        markers) was spliced between prefix and suffix, declaring everything twice; and a
        fence line nested inside the block survived into the source. Now fence lines are
        dropped, the innermost marker pair wins, and a body that repeats the prefix is taken as
        the whole program the model evidently wrote.
        """
        raw = reply or ""
        code = extract_code(raw)
        flat = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("```"))
        # The marker pair is looked for inside the fenced code first, then on the fence-less
        # reply (a fence nested inside the block used to cut the code at the wrong place). A pair
        # with nothing between it -- a trailing sentence like "both EVOLVE-BLOCK-START and
        # EVOLVE-BLOCK-END markers are kept" -- is skipped, not taken as an empty block.
        body = None
        for cand in ([code] if code else []) + [flat]:
            body = self._find_block(cand)
            if body:
                break
        if body is None:
            body = "\n".join(ln for ln in (code or raw.strip()).splitlines()
                              if not ln.strip().startswith("```"))
        body = body.strip("\n")
        if not body:
            return None
        if self._looks_like_junk(body):
            return None
        if self._is_whole_program(body) or "EVOLVE-BLOCK" in body:
            # A whole-file rewrite, or a body that still carries marker lines, is not a block
            # edit. Splicing it declares everything twice; keeping it whole runs a from-scratch
            # program that scores near zero (87% of them on AHC039). Upstream SimpleTES rejects
            # such replies; so do we -- the caller re-rolls once, and the budget is not spent on
            # an evaluation that cannot inform the search.
            return None
        return f"{self.prefix}\n{body}\n{self.suffix}"

    _C_LIKE = re.compile(r"^\s*#\s*include\b", re.M)
    _MAIN = re.compile(r"^\s*(?:int|auto|void)\s+main\s*\(", re.M)

    @staticmethod
    def _find_block(text: str) -> Optional[str]:
        """The text between the last START marker that has a non-empty body before its END."""
        pos = len(text)
        while True:
            s = text.rfind("EVOLVE-BLOCK-START", 0, pos)
            if s == -1:
                return None
            e = text.find("EVOLVE-BLOCK-END", s + 1)
            nl = text.find("\n", s)
            if e != -1 and nl != -1 and nl < e:
                body = text[nl + 1: text.rfind("\n", 0, e) + 1].strip("\n")
                if body.strip():
                    return "\n".join(ln for ln in body.splitlines()
                                     if not ln.strip().startswith("```")).strip("\n")
            pos = s

    def _is_whole_program(self, body: str) -> bool:
        """A block body that opens with #include, defines main(), or repeats the prefix's opening
        lines is the whole file the model wrote, not the block -- splicing it would declare
        everything twice (28% of AHC039 evaluations before this check)."""
        head = body.lstrip()
        if head.startswith(("#include", "#pragma", "#define")) and self._C_LIKE.search(self.prefix):
            return True
        if self._MAIN.search(body) and self._MAIN.search(self.suffix or ""):
            return True
        return self._repeats_prefix(body)

    def diagnose(self, reply: str) -> str:
        """One line describing why merge() returned None for `reply` (for the failure log)."""
        raw = reply or ""
        flat = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("```"))
        code = extract_code(raw)
        body = None
        for cand in ([code] if code else []) + [flat]:
            body = self._find_block(cand)
            if body:
                break
        if body is None:
            body = "\n".join(ln for ln in (code or raw.strip()).splitlines() if not ln.strip().startswith("```")).strip("\n")
        return (f"len={len(raw)} fences={raw.count('```')} starts={raw.count('EVOLVE-BLOCK-START')} "
                f"ends={raw.count('EVOLVE-BLOCK-END')} fenced_code={'yes' if code else 'no'} body_len={len(body)} "
                f"junk={'yes' if body and self._looks_like_junk(body) else 'no'} "
                f"head={raw.strip()[:60]!r} tail={raw.strip()[-80:]!r}")

    def _looks_like_junk(self, body: str) -> bool:
        """For a C-like seed, a body without a single statement or brace is prose, a formula,
        or code in another language; better rejected (and re-rolled) than compiled."""
        if not self._C_LIKE.search(self.prefix):
            return False
        return ";" not in body and "{" not in body

    def _repeats_prefix(self, body: str) -> bool:
        """True when `body` contains the prefix's opening lines in order -- a whole-file reply."""
        sig = [ln.strip() for ln in self.prefix.splitlines()
               if ln.strip() and "EVOLVE-BLOCK" not in ln][:6]
        if len(sig) < 3:
            return False
        pos = 0
        hits = 0
        for ln in sig:
            i = body.find(ln, pos)
            if i != -1:
                hits += 1
                pos = i + len(ln)
        return hits >= min(3, len(sig))


class CompletionVariator:
    """One prompt, `n=k` completions, one code block from each.

    Shared machinery only: the call (with its n-shortfall fallback and usage booking), file
    selection, EVOLVE-BLOCK splitting/merging, and a GENERIC prompt. A method whose operator
    speaks differently subclasses this and overrides `SYSTEM` (`""` = send no system message)
    and/or the prompt builders -- see `UpstreamCompletionVariator` in `methods/simpletes.py`.
    """

    SYSTEM = DEFAULT_SYSTEM

    def __init__(
        self,
        *,
        model: str = "high",
        system_prompt: Optional[str] = None,
        target_file: Optional[str] = None,
        temperature: float = 1.0,
        max_tokens: Optional[int] = None,
        timeout: float = 600,
        score_key: str = "combined_score",
        max_parent_chars: int = 24000,
        reasoning_max_tokens: Optional[int] = None,
        reply_retries: int = 1,
        reasoning_off: bool = False,
    ):
        self.reasoning_max_tokens = reasoning_max_tokens
        self.reply_retries = max(0, int(reply_retries))
        self.reasoning_off = bool(reasoning_off)
        self._last_reasoning: List[str] = []
        """Fresh rolls per candidate whose reply had no usable code (an empty reply from a
        reasoning model that spent its budget thinking). Each roll is a normal LLM call and is
        booked against the arm's call budget; it only decides how many tries a candidate slot
        gets before it is given up."""
        """Cap on a reasoning model's internal thinking, when the provider supports it.

        Not a style preference -- a liveness fix. On the Erdos task deepseek-v4-flash spends
        its ENTIRE 32768-token output budget thinking (measured: 101k characters of reasoning,
        `finish_reason=length`) and emits zero characters of program, every call. Reserving
        room for the answer is the same class of provider accommodation as `max_tokens`
        itself, and the token ledger records what it actually cost."""
        self.model = model
        # None -> the class's own words; "" -> send NO system message at all
        self.system_prompt = self.SYSTEM if system_prompt is None else system_prompt
        self.target_file = target_file
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.score_key = score_key
        self.max_parent_chars = max_parent_chars

    # ---- prompt ----------------------------------------------------------

    _LANG = {".cpp": ("C++", "cpp"), ".cc": ("C++", "cpp"), ".py": ("Python", "python"),
             ".rs": ("Rust", "rust"), ".go": ("Go", "go")}

    def _lang(self, path: str):
        from os.path import splitext

        return self._LANG.get(splitext(path)[1], ("", ""))

    def _file_to_evolve(self, files: Dict[str, str]) -> str:
        if self.target_file and self.target_file in files:
            return self.target_file
        for pref in ("main.py", "solution.py", "program.py"):
            if pref in files:
                return pref
        py = sorted(p for p in files if p.endswith(".py"))
        return py[0] if py else next(iter(files), "main.py")

    def build_prompt(self, ctx: EvolveContext, item: Create, path: str,
                     content: str) -> str:
        c = item.context
        parts = [c.instruction or ctx.objective or "Improve the program."]

        def _score(ind):
            v = ind.metrics().get(self.score_key)
            return f" ({self.score_key} = {v})" if v is not None else ""

        def _body(text: str, primary: bool = False) -> str:
            # A program the model is asked to REWRITE is never truncated: on AHC039 the 43KB
            # seed was silently cut at 24KB, so 189 completions rewrote a program they had seen
            # barely half of -- and every one of them wrecked it. That number then masqueraded
            # as a result about the algorithm. The cap protects the prompt from a pile of large
            # REFERENCE parents; the primary parent is the one thing it must never touch.
            if primary or len(text) <= self.max_parent_chars:
                return text
            return text[: self.max_parent_chars] + "\n# ... truncated ...\n"

        if len(c.parents) > 1:
            # Several parents means the method is asking for a synthesis, not an edit: the
            # programs are peers to learn from and none of them is "the current one". SimpleTES
            # works this way -- its selected set IS its parent set, and its prompt shows them as
            # references before asking for a new program -- so presenting the first as the
            # incumbent would quietly turn a recombination into a mutation.
            parts.append(f"\n## Reference programs ({len(c.parents)} of them), best first")
            for i, p in enumerate(c.parents):
                src = p.genome.files.get(path) if isinstance(p.genome, CodeGenome) else None
                parts.append(f"\n### reference {i + 1}{_score(p)}")
                # the best-first reference is what the new program most plausibly builds on;
                # it gets the primary guarantee, the rest absorb the cap
                parts.append(f"```python\n{_body(src or p.genome.render(), primary=i == 0)}```")
            parts.append("\nWrite a NEW program, better than all of them. Prefer an approach none "
                         "of them takes; combine what works where that helps.")
        else:
            parent = c.parents[0] if c.parents else None
            parts.append(f"\n## Current program `{path}`"
                         + (_score(parent) if parent is not None else ""))
            parts.append(f"```python\n{_body(content, primary=True)}```")
        if c.history:
            parts.append(f"\n## What earlier attempts scored\n{c.history}")
        if c.inspirations:
            lines = []
            for i in c.inspirations[:3]:
                s = i.metrics().get(self.score_key)
                summary = i.meta.get("summary", "")
                lines.append(f"- #{i.order}: {self.score_key}="
                             f"{s if s is not None else '?'} {summary}".rstrip())
            parts.append("\n## Other attempts in this run\n" + "\n".join(lines))
        if c.failures:
            worst = sorted(c.failures.items(), key=lambda kv: -kv[1])[:5]
            parts.append("\n## Recurring failures to avoid\n" +
                         "\n".join(f"- {k} (x{int(v)})" for k, v in worst))
        parts.append(f"\nReply with the complete new `{path}` in one fenced code block.")
        return "\n".join(parts)

    def build_block_prompt(self, ctx: EvolveContext, item: Create, path: str,
                           eb: "EvolveBlock") -> str:
        """Generic block prompt for a marker-carrying program: the model regenerates ONLY the
        evolve block; prefix and suffix are shown and kept verbatim."""
        c = item.context

        def _score(ind):
            v = ind.metrics().get(self.score_key)
            return f" ({self.score_key} = {v})" if v is not None else ""

        parts = [c.instruction or ctx.objective or "Improve the program.",
                 "\nGeneration instruction (must follow exactly):\n"
                 f"1) Only the code between `{eb.start_line}` and `{eb.end_line}` is extracted.\n"
                 "2) The final program is reconstructed as EXACT_PREFIX + evolved_block + "
                 "EXACT_SUFFIX.\n"
                 "3) Keep marker lines exactly as written.\n"
                 "4) Return one code block that includes both EVOLVE-BLOCK markers.",
                 f"\nEXACT_PREFIX (kept unchanged):\n```\n{eb.prefix}\n```",
                 f"\nEXACT_SUFFIX (kept unchanged):\n```\n{eb.suffix}\n```"]
        refs = c.parents if len(c.parents) > 1 else c.parents[:1]
        for i, p in enumerate(refs):
            src = p.genome.files.get(path) if isinstance(p.genome, CodeGenome) else None
            block = EvolveBlock(src).block if src else p.genome.render()
            title = ("current evolve block" if len(refs) == 1
                     else f"reference {i + 1} evolve block")
            parts.append(f"\n### {title}{_score(p)}\n```\n{block.strip()}\n```")
        if c.history:
            parts.append(f"\n## What earlier attempts scored\n{c.history}")
        if c.failures:
            worst = sorted(c.failures.items(), key=lambda kv: -kv[1])[:5]
            parts.append("\n## Recurring failures to avoid\n" +
                         "\n".join(f"- {k} (x{int(v)})" for k, v in worst))
        parts.append("\n=== GENERATION STRATEGY ===\n"
                     "- Prioritize NOVEL approaches not yet seen above\n"
                     "- Only refine existing approaches if you identify clear improvement "
                     "potential\n"
                     "- Combine insights from multiple solutions when beneficial\n\n"
                     "Generate an improved solution with higher score:")
        return "\n".join(parts)

    # ---- the call --------------------------------------------------------

    async def _complete(self, prompt: str, k: int, *, continue_from: Optional[str] = None) -> List[str]:
        """One request, k completions. Returns the raw texts.

        A reasoning model behind an OpenAI-compatible proxy can spend the whole output budget
        thinking and return no content; the reasoning it produced comes back beside the empty
        content. `self._last_reasoning` keeps it per choice so the caller can ask for a
        continuation: `continue_from=<reasoning>` re-sends the prompt with that analysis as the
        assistant's own words and thinking disabled, so the model writes the answer it already
        planned (measured: a usable near-seed edit in ~90 s where a fresh roll costs ~10 min)."""
        from openai import AsyncOpenAI

        from pantheon.utils.llm_providers import detect_provider

        cfg = detect_provider(self.model, False)
        client = AsyncOpenAI(
            api_key=cfg.api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=cfg.base_url or os.environ.get("OPENAI_API_BASE") or None,
            timeout=self.timeout,
        )
        msgs = ([{"role": "user", "content": prompt}] if not self.system_prompt else
                [{"role": "system", "content": self.system_prompt},
                 {"role": "user", "content": prompt}])
        if continue_from:
            msgs = msgs + continuation_messages(continue_from)
        kwargs: Dict[str, Any] = {"model": cfg.model_name, "messages": msgs}
        if k > 1:
            kwargs["n"] = k
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens
        extra: Dict[str, Any] = {"usage": {"include": True}}
        """OpenRouter returns the call's actual price under `usage.cost` when asked. Without
        it the OpenAI-protocol response carries no price at all, so every completion-operator
        run reported $0.00 spend -- SimpleTES looked free next to agent arms billed at $4-25."""
        if self.reasoning_max_tokens:
            extra["reasoning"] = {"max_tokens": self.reasoning_max_tokens}
        if self.reasoning_off or continue_from:
            extra["reasoning"] = {"enabled": False}   # hybrid models answer without thinking
        kwargs["extra_body"] = extra
        resp = await client.chat.completions.create(**kwargs)
        from .usage import add_response
        add_response(resp)
        choices = resp.choices or []
        self._last_reasoning = [(getattr(ch.message, "reasoning", None) or "") for ch in choices]
        return [(ch.message.content or "") for ch in choices]

    async def create(self, ctx: EvolveContext, item: Create) -> List[Produced]:
        parent = item.context.parents[0] if item.context.parents else (
            ctx.store.get(item.parent_ids[0]) if item.parent_ids else None)
        if parent is None or not isinstance(parent.genome, CodeGenome):
            logger.warning("CompletionVariator needs a CodeGenome parent; got %r",
                           type(getattr(parent, "genome", None)))
            return []

        files = dict(parent.genome.files)
        path = self._file_to_evolve(files)
        eb = EvolveBlock(files.get(path, ""))
        if eb.has_markers:
            prompt = self.build_block_prompt(ctx, item, path, eb)
        else:
            prompt = self.build_prompt(ctx, item, path, files.get(path, ""))

        t0 = time.time()
        try:
            texts = await self._complete(prompt, item.k)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{item.id}] completion failed: {type(e).__name__}: {e}")
            return []
        if len(texts) < item.k:
            # providers vary on whether they honour n; fill the rest with extra requests rather
            # than letting the method see a shortfall that is really a provider quirk
            import asyncio

            extra = await asyncio.gather(
                *(self._complete(prompt, 1) for _ in range(item.k - len(texts))),
                return_exceptions=True)
            for r in extra:
                if isinstance(r, list):
                    texts.extend(r)

        out: List[Produced] = []
        for i, text in enumerate(texts[: item.k]):
            code = eb.merge(text) if eb.has_markers else extract_code(text)
            reasoning = (getattr(self, "_last_reasoning", None) or [""] * len(texts))
            reasoning = reasoning[i] if i < len(reasoning) else ""
            if not code and not (text or "").strip() and reasoning.strip():
                logger.warning(f"[{item.id}#{i}] empty reply with {len(reasoning)} chars of reasoning; "
                               "continuing from it with thinking off")
                try:
                    text = (await self._complete(prompt, 1, continue_from=reasoning))[0]
                    code = eb.merge(text) if eb.has_markers else extract_code(text)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[{item.id}#{i}] continuation failed: {type(e).__name__}: {e}")
            tries = 0
            while not code and tries < self.reply_retries:
                # A fresh roll per failed candidate. Reasoning models sometimes spend the
                # output budget thinking and end without a parseable block; that is a provider
                # quirk, and a method comparison should not book it as the algorithm finding
                # nothing -- the same reasoning as the n-shortfall fallback above.
                tries += 1
                logger.warning(f"[{item.id}#{i}] no code block in the reply; retry {tries}/{self.reply_retries}"
                               + (f" | {eb.diagnose(text)}" if eb.has_markers else ""))
                _dump_reply(text, f"{item.id}-{i}-{tries}")
                try:
                    text = (await self._complete(prompt, 1))[0]
                    code = eb.merge(text) if eb.has_markers else extract_code(text)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[{item.id}#{i}] retry failed: {type(e).__name__}: {e}")
            if not code:
                logger.warning(f"[{item.id}#{i}] no code block after retry"
                               + (f" | {eb.diagnose(text)}" if eb.has_markers else ""))
                _dump_reply(text, f"{item.id}-{i}-final")
                continue
            child = dict(files)
            child[path] = code
            out.append(Produced(
                genome=CodeGenome(files=child),
                item_id=item.id,
                batch_id=item.batch_id,
                parent_ids=list(item.parent_ids),
                anchor_id=item.anchor_id,
                meta={"summary": _first_line(text), "candidate": i,
                      "mutation_seconds": time.time() - t0},
            ))
        return out


def continuation_messages(reasoning: str, limit: int = 120_000) -> List[Dict[str, str]]:
    """The two turns appended to a prompt to continue from an analysis that was cut off."""
    return [{"role": "assistant",
             "content": "(My analysis so far, cut off before the final answer:)\n\n" + reasoning[-limit:]},
            {"role": "user",
             "content": "Your analysis above was cut off before you wrote the answer. Do not analyse "
                        "further. Using the plan you already made, output ONLY the final answer now, "
                        "in the exact format the instructions require (one fenced code block; keep "
                        "both EVOLVE-BLOCK marker lines if the program has them)."}]


def _dump_reply(text: str, tag: str) -> None:
    """When EVOLVE_REPLY_DUMP names a directory, keep the raw reply a merge rejected."""
    d = os.environ.get("EVOLVE_REPLY_DUMP")
    if not d:
        return
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{tag}.txt"), "w") as fh:
            fh.write(text or "")
    except OSError:
        pass


def _first_line(text: str) -> str:
    """A one-line description from whatever the model said outside the code block."""
    for line in (text or "").splitlines():
        s = line.strip()
        if s and not s.startswith("```"):
            return s[:200]
    return ""
