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
        """
        code = extract_code(reply) or (reply or "").strip()
        if not code:
            return None
        s = code.find("EVOLVE-BLOCK-START")
        e = code.find("EVOLVE-BLOCK-END")
        if s != -1 and e != -1 and e > s:
            body = code[code.index("\n", s) + 1: code.rfind("\n", 0, e) + 1]
        else:
            body = code
        body = body.strip("\n")
        if not body:
            return None
        return f"{self.prefix}\n{body}\n{self.suffix}"


class CompletionVariator:
    """One prompt, `n=k` completions, one code block from each."""

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
        upstream_style: bool = False,
    ):
        """`upstream_style=True` reproduces the SimpleTES authors' generation query for
        marker-carrying seeds (their `GENERATION_PROMPT_TEMPLATE`, commit a19a54b1): no system
        message at all, `Task:` header, a language-tagged reply instruction, inspirations as
        FULL programs with their complete metric dicts, their section headers and their
        4-bullet strategy. `system_prompt=""` also means "send no system message" on its own.
        What it does NOT reproduce is upstream's per-node reflection paragraphs -- those are an
        engine feature (one extra LLM call per evaluated node), not prompt text."""
        self.model = model
        self.upstream_style = upstream_style
        if system_prompt is not None:
            self.system_prompt = system_prompt          # "" = no system message
        else:
            self.system_prompt = "" if upstream_style else DEFAULT_SYSTEM
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

    def _upstream_inspiration(self, index: int, ind, code: str, tag: str) -> str:
        """One inspiration, upstream's `INSPIRATION_TEMPLATE`: full metrics, full code."""
        m = ind.metrics() or {}
        lines = []
        for k, v in m.items():
            if k == "error":
                lines.append(f"  {k}: {str(v)[:240]}")
            elif isinstance(v, float):
                lines.append(f"  {k}: {v:.6f}")
            else:
                lines.append(f"  {k}: {v}")
        return (f"\n--- Inspiration {index} ---\n"
                f"Score: {m.get(self.score_key)}\n"
                f"Metrics:\n" + "\n".join(lines) +
                f"\nCode:\n```{tag}\n{code}\n```\n")

    def build_upstream_block_prompt(self, ctx: EvolveContext, item: Create, path: str,
                                    eb: "EvolveBlock") -> str:
        """The authors' `GENERATION_PROMPT_TEMPLATE`, reproduced: same headers, same rule list
        (language-named), same inspiration blocks -- each parent as its FULL program with its
        complete metric dict, sorted by score -- same failure-pattern section and the same
        four-bullet strategy. No chain-history digest: upstream carries history through the
        inspirations, so adding ours would be a departure, not a translation."""
        c = item.context
        lang_name, tag = self._lang(path)

        def _sc(ind):
            v = ind.metrics().get(self.score_key)
            return v if v is not None else float("-inf")

        insp = sorted(c.parents, key=_sc, reverse=True)
        chunks = []
        for i, pr in enumerate(insp, 1):
            src = pr.genome.files.get(path) if isinstance(pr.genome, CodeGenome) else None
            chunks.append(self._upstream_inspiration(i, pr, src or pr.genome.render(), tag))
        failure_text = ""
        if c.failures:
            worst = sorted(c.failures.items(), key=lambda kv: -kv[1])[:5]
            failure_text = ("\n[FAILURE PATTERNS] (common errors to avoid)\n" +
                            "\n".join(f"- {k} (x{int(v)})" for k, v in worst) + "\n")
        block_word = f"{lang_name} code block" if lang_name else "code block"
        return (
            f"Task: {c.instruction or ctx.objective}\n\n"
            "Generation instruction (must follow exactly):\n"
            f"1) Only the code between `{eb.start_line}` and `{eb.end_line}` is extracted.\n"
            "2) The final program is reconstructed as EXACT_PREFIX + evolved_block + "
            "EXACT_SUFFIX.\n"
            "3) Keep marker lines exactly as written.\n"
            f"4) Return one {block_word} that includes both EVOLVE-BLOCK markers.\n\n"
            f"EXACT_PREFIX (kept unchanged):\n```{tag}\n{eb.prefix.rstrip(chr(10))}\n```\n\n"
            f"EXACT_SUFFIX (kept unchanged):\n```{tag}\n{eb.suffix.rstrip(chr(10))}\n```\n\n"
            "=== REFERENCE SOLUTIONS ===\n\n"
            f"[SAMPLED INSPIRATIONS] ({len(insp)} solutions sampled for detailed reference)\n"
            "Learn from these specific implementations - study their patterns and techniques.\n"
            + "".join(chunks) + failure_text +
            "\n=== GENERATION STRATEGY ===\n"
            "- Prioritize NOVEL approaches not yet seen in the elite pool\n"
            "- Only refine existing approaches if you identify clear improvement potential\n"
            "- Combine insights from multiple solutions when beneficial\n"
            "- Avoid the listed failure patterns\n\n"
            "Generate an improved solution with higher score:\n")

    def build_block_prompt(self, ctx: EvolveContext, item: Create, path: str,
                           eb: "EvolveBlock") -> str:
        """Upstream's generation prompt, for a marker-carrying program: the model regenerates
        ONLY the evolve block; prefix and suffix are shown and kept verbatim."""
        if self.upstream_style:
            return self.build_upstream_block_prompt(ctx, item, path, eb)
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

    async def _complete(self, prompt: str, k: int) -> List[str]:
        """One request, k completions. Returns the raw texts."""
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
        kwargs: Dict[str, Any] = {"model": cfg.model_name, "messages": msgs}
        if k > 1:
            kwargs["n"] = k
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens
        resp = await client.chat.completions.create(**kwargs)
        from .usage import add_response
        add_response(resp)
        return [(ch.message.content or "") for ch in (resp.choices or [])]

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
            if not code:
                logger.warning(f"[{item.id}#{i}] no code block in the reply")
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


def _first_line(text: str) -> str:
    """A one-line description from whatever the model said outside the code block."""
    for line in (text or "").splitlines():
        s = line.strip()
        if s and not s.startswith("```"):
            return s[:200]
    return ""
