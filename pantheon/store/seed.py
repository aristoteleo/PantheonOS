"""Batch seed the Pantheon Store with factory and external skills.

Two modes:
  - prepare: Collect all packages into a local directory + manifest.json
  - publish: Read from prepared directory and batch-publish to Hub API
"""

import asyncio
import hashlib
import json
import posixpath
import re
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import frontmatter
from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table

from .client import StoreClient


console = Console()


# --- Category mapping for factory skills ---

FACTORY_SKILL_CATEGORY = {
    "omics/quality_control": "single-cell",
    "omics/cell_type_annotation": "single-cell",
    "omics/trajectory_inference": "single-cell",
    "omics/single_cell_spatial_mapping": "spatial-omics",
    "omics/visualize_3d_spatial": "spatial-omics",
    "omics/environment_management": "environment",
    "omics/parallel_computing": "environment",
}

FACTORY_SKILL_CATEGORY_PREFIX = {
    "omics/scfm": "foundation-models",
    "omics/database_access": "bioinformatics",
    "omics/sc_best_practices": "best-practices",
    "omics/upstream_processing": "upstream-processing",
}

# Category mapping for LabClaw subdirectories
LABCLAW_CATEGORY = {
    "bio": "bioinformatics",
    "general": "data-science",
    "literature": "literature",
    "med": "medical",
    "pharma": "drug-discovery",
    "vision": "computer-vision",
}

# --- External repo configs ---

EXTERNAL_REPOS = {
    "labclaw": {
        "url": "https://github.com/wu-yc/LabClaw.git",
        "skills_dir": "skills",
        "display_name": "LabClaw",
        "source_url": "https://github.com/wu-yc/LabClaw",
        "has_categories": True,
    },
    "openclaw-medical": {
        "url": "https://github.com/FreedomIntelligence/OpenClaw-Medical-Skills.git",
        "skills_dir": "skills",
        "display_name": "OpenClaw Medical Skills",
        "source_url": "https://github.com/FreedomIntelligence/OpenClaw-Medical-Skills",
        "has_categories": False,
    },
    "claude-scientific": {
        "url": "https://github.com/K-Dense-AI/claude-scientific-skills.git",
        "skills_dir": "scientific-skills",
        "display_name": "Claude Scientific Skills",
        "source_url": "https://github.com/K-Dense-AI/claude-scientific-skills",
        "has_categories": False,
    },
    "clawbio": {
        "url": "https://github.com/ClawBio/ClawBio.git",
        "skills_dir": "skills",
        "display_name": "ClawBio",
        "source_url": "https://github.com/ClawBio/ClawBio",
        "has_categories": False,
    },
    "omicclaw": {
        "url": "https://github.com/Starlitnightly/omicclaw.git",
        "skills_dir": "src/omicverse_skills/skills",
        "display_name": "OmicClaw",
        "source_url": "https://github.com/Starlitnightly/omicclaw",
        "has_categories": False,
    },
    "bioskills": {
        # GPTomics/bioSkills — 540+ SKILL.md across 60+ bioinformatics categories (MIT).
        # Category folders live at the REPO ROOT (e.g. differential-expression/, chip-seq/),
        # so skills_dir is "." : <category>/<skill-name>/SKILL.md.
        "url": "https://github.com/GPTomics/bioSkills.git",
        "skills_dir": ".",
        "display_name": "bioSkills",
        "source_url": "https://github.com/GPTomics/bioSkills",
        "has_categories": True,
    },
    "sciagent": {
        # jaechang-hits/SciAgent-Skills — ~197 skills (CC-BY-4.0); 92% on BixBench-Verified-50.
        # Curated skills under skills/<category>/<skill-name>/SKILL.md (legacy/ is excluded).
        "url": "https://github.com/jaechang-hits/SciAgent-Skills.git",
        "skills_dir": "skills",
        "display_name": "SciAgent-Skills",
        "source_url": "https://github.com/jaechang-hits/SciAgent-Skills",
        "has_categories": True,
    },
}


def _slugify(name: str) -> str:
    """Convert a name to a valid skill ID slug."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\-_]", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def _bump_patch(version: str) -> str:
    """Increment the patch component of a semver-ish string: 1.0.0 -> 1.0.1."""
    parts = (version or "1.0.0").split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
        return ".".join(parts)
    except (ValueError, IndexError):
        return (version or "1.0.0") + ".1"


def _git_meta(path) -> Tuple[Optional[str], Optional[str]]:
    """Return (commit_sha, committed_at_iso) for the git repo containing `path`,
    or (None, None) if it is not a git checkout. Used to record which upstream
    revision a package was ingested from, so the store can detect when an upstream
    source has moved ahead of what we hold."""
    import subprocess
    try:
        p = str(path)
        rev = subprocess.run(["git", "-C", p, "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        date = subprocess.run(["git", "-C", p, "log", "-1", "--format=%cI"],
                              capture_output=True, text=True, check=True).stdout.strip()
        return (rev or None, date or None)
    except Exception:
        return (None, None)


def _dedup_by_store_name(skills: list, source_name: str = "") -> list:
    """Resolve store_name collisions. A repo can give two DIFFERENT skills the
    same `name:` frontmatter (e.g. OpenClaw `pptx` and `pptx-official` both
    declare name: pptx -> both derive `openclaw-medical_pptx`).

    - Exact duplicate (identical content + files): drop the extra.
    - Same name but DIFFERENT content: keep both, suffix the later one
      (`..._pptx`, `..._pptx-2`) so each distinct skill gets a distinct id.
    """
    def _hash(s):
        return hashlib.md5(
            (s.get("content", "") + json.dumps(s.get("files", {}), sort_keys=True)).encode()
        ).hexdigest()

    seen: Dict[str, str] = {}  # store_name -> content hash
    out, dropped, renamed = [], 0, 0
    for s in skills:
        n = s["store_name"]
        h = _hash(s)
        if n not in seen:
            seen[n] = h
            out.append(s)
            continue
        if seen[n] == h:
            dropped += 1          # exact duplicate
            continue
        i = 2                      # different skill, same name -> disambiguate
        while f"{n}-{i}" in seen:
            i += 1
        s["store_name"] = f"{n}-{i}"
        s["display_name"] = f"{s.get('display_name', n)} ({i})"
        seen[s["store_name"]] = h
        out.append(s)
        renamed += 1
    if dropped or renamed:
        logger.info(f"[{source_name or 'factory'}] dedup: dropped {dropped} exact dup(s), "
                    f"renamed {renamed} same-name-different skill(s)")
    return out


# --- Skill cross-reference extraction (resolved to exact store names at ingest) ---
_REL_LINK_RE = re.compile(r"\]\((\.\.?/[^)\s]+?\.md)\)", re.I)
_RELATED_HDR_RE = re.compile(r"^\s*#{1,5}\s*(related skills|see also|related)\b", re.I)
_HDR_RE = re.compile(r"^\s*#{1,5}\s")
_BULLET_RE = re.compile(r"^\s*[-*]\s*`?([A-Za-z0-9][\w./-]*)`?")


def _extract_refs(content: str) -> Tuple[List[str], List[str]]:
    """From a skill's markdown, return (relative_link_paths, related_skill_names).

    Two referencing styles: relative links like `[x](./single_cell/SKILL.md)`
    and name bullets under a `## Related Skills` / `## See Also` heading.
    """
    text = content or ""
    rel_paths = _REL_LINK_RE.findall(text)
    names: List[str] = []
    in_section = False
    for ln in text.splitlines():
        if _RELATED_HDR_RE.match(ln):
            in_section = True
            continue
        if in_section:
            if _HDR_RE.match(ln):
                break
            m = _BULLET_RE.match(ln)
            if m:
                names.append(m.group(1))
    return rel_paths, names


def _get_factory_category(rel_path: str) -> str:
    """Get category for a factory skill based on its relative path."""
    if rel_path in FACTORY_SKILL_CATEGORY:
        return FACTORY_SKILL_CATEGORY[rel_path]
    for prefix, category in FACTORY_SKILL_CATEGORY_PREFIX.items():
        if rel_path.startswith(prefix):
            return category
    return "general"


def _run(coro):
    """Run an async coroutine synchronously."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


class StoreSeed:
    """Batch seed the Pantheon Store with initial content."""

    def __init__(self, hub_url: str = None):
        self.client = StoreClient(hub_url=hub_url)
        self.stats = {"published": 0, "updated": 0, "skipped": 0, "failed": 0}

    def _reset_stats(self):
        self.stats = {"published": 0, "updated": 0, "skipped": 0, "failed": 0}

    # ------------------------------------------------------------------ #
    #  Factory discovery                                                   #
    # ------------------------------------------------------------------ #

    def _discover_factory_skills(self):
        """Discover all publishable skill files in factory/templates/skills/.

        Returns both individual skills AND skill groups (directories with SKILL.md).
        Skill groups bundle their direct sibling .md files in the `files` dict.
        """
        factory_dir = Path(__file__).parent.parent / "factory" / "templates" / "skills"
        skills = []

        # --- Individual skills (non-index .md files) ---
        for md_file in sorted(factory_dir.rglob("*.md")):
            rel = md_file.relative_to(factory_dir)
            if any(p.startswith("_") or p.startswith(".") for p in rel.parts[:-1]):
                continue
            if md_file.name in ("SKILL.md", "SKILLS.md"):
                continue

            try:
                post = frontmatter.load(str(md_file))
            except Exception:
                continue

            skill_id = post.get("id", md_file.stem)
            name = post.get("name", skill_id)
            description = post.get("description", "")
            tags = post.get("tags", [])

            rel_no_ext = str(rel.with_suffix("")).replace("\\", "/")
            store_name = rel_no_ext.replace("/", "_")
            category = _get_factory_category(rel_no_ext)

            skills.append({
                "store_name": store_name,
                "display_name": name,
                "description": description.strip() if isinstance(description, str) else str(description).strip(),
                "category": category,
                "tags": tags if isinstance(tags, list) else [],
                "content": frontmatter.dumps(post),
                "source": "factory",
                "_relkey": rel_no_ext,
                "_basedir": posixpath.dirname(rel_no_ext),
                "_is_group": False,
            })

        # --- Skill groups (directories with SKILL.md index) ---
        for skill_md in sorted(factory_dir.rglob("SKILL.md")):
            skill_dir = skill_md.parent
            rel_dir = skill_dir.relative_to(factory_dir)
            if any(p.startswith("_") or p.startswith(".") for p in rel_dir.parts):
                continue

            try:
                post = frontmatter.load(str(skill_md))
            except Exception:
                continue

            skill_id = post.get("id", skill_dir.name + "_index")
            name = post.get("name", skill_id)
            description = post.get("description", "")
            tags = post.get("tags", [])

            rel_dir_str = str(rel_dir).replace("\\", "/")
            store_name = rel_dir_str.replace("/", "_") + "_group"
            category = _get_factory_category(rel_dir_str)

            # Bundle direct sibling files (not SKILL.md, not entering sub-groups)
            # Sub-directories that have their own SKILL.md are separate groups
            sub_group_dirs = {
                d for d in skill_dir.iterdir()
                if d.is_dir() and (d / "SKILL.md").exists()
            }
            files: Dict[str, str] = {}
            for child in sorted(skill_dir.iterdir()):
                if child.is_dir():
                    # Skip sub-group dirs and hidden/underscore dirs
                    if child in sub_group_dirs or child.name.startswith(("_", ".")):
                        continue
                    # Recursively collect files from non-group subdirs
                    for sub_file in sorted(child.rglob("*")):
                        if not sub_file.is_file():
                            continue
                        sub_rel = sub_file.relative_to(factory_dir)
                        if any(p.startswith(("_", ".")) or p == "__pycache__" for p in sub_rel.parts):
                            continue
                        try:
                            content_text = sub_file.read_text(encoding="utf-8")
                        except (UnicodeDecodeError, PermissionError):
                            continue
                        file_key = f"skills/{str(sub_rel).replace(chr(92), '/')}"
                        files[file_key] = content_text
                elif child.is_file():
                    if child.name in ("SKILL.md", "SKILLS.md"):
                        continue
                    try:
                        content_text = child.read_text(encoding="utf-8")
                    except (UnicodeDecodeError, PermissionError):
                        continue
                    child_rel = child.relative_to(factory_dir)
                    file_key = f"skills/{str(child_rel).replace(chr(92), '/')}"
                    files[file_key] = content_text

            skills.append({
                "store_name": store_name,
                "display_name": name,
                "description": description.strip() if isinstance(description, str) else str(description).strip(),
                "category": category,
                "tags": tags if isinstance(tags, list) else [],
                "content": frontmatter.dumps(post),
                "files": files,
                "source": "factory",
                "_relkey": rel_dir_str,
                "_basedir": rel_dir_str,
                "_is_group": True,
            })

        # Resolve relative-link references to exact store names (index→sub-skill graph)
        indiv_by_path = {s["_relkey"]: s["store_name"] for s in skills if not s["_is_group"]}
        group_by_dir = {s["_relkey"]: s["store_name"] for s in skills if s["_is_group"]}
        for s in skills:
            refs: List[str] = []
            rel_paths, _names = _extract_refs(s["content"])
            for rp in rel_paths:
                tgt = posixpath.normpath(posixpath.join(s["_basedir"], rp))
                if tgt.endswith("/SKILL.md"):
                    r = group_by_dir.get(tgt[: -len("/SKILL.md")])
                elif tgt == "SKILL.md":
                    r = group_by_dir.get(s["_basedir"])
                else:
                    key = tgt[:-3] if tgt.endswith(".md") else tgt
                    r = indiv_by_path.get(key) or group_by_dir.get(key)
                if r and r != s["store_name"]:
                    refs.append(r)
            s["references"] = list(dict.fromkeys(refs))
            for k in ("_relkey", "_basedir", "_is_group"):
                s.pop(k, None)

        return _dedup_by_store_name(skills, "factory")

    def _discover_factory_agents(self):
        """Discover agent files in factory/templates/agents/."""
        agents_dir = Path(__file__).parent.parent / "factory" / "templates" / "agents"
        agents = []

        for md_file in sorted(agents_dir.glob("*.md")):
            try:
                post = frontmatter.load(str(md_file))
            except Exception:
                continue

            agent_id = post.get("id", md_file.stem)
            name = post.get("name", agent_id)
            description = post.get("description", "")

            agents.append({
                "store_name": agent_id,
                "display_name": name,
                "description": description.strip() if isinstance(description, str) else str(description).strip(),
                "category": "general",
                "tags": [],
                "content": frontmatter.dumps(post),
                "source": "factory",
            })

        return agents

    def _discover_factory_teams(self):
        """Discover team files in factory/templates/teams/."""
        from .publisher import PackageCollector

        teams_dir = Path(__file__).parent.parent / "factory" / "templates" / "teams"
        collector = PackageCollector()
        teams = []

        for md_file in sorted(teams_dir.glob("*.md")):
            try:
                post = frontmatter.load(str(md_file))
            except Exception:
                continue

            team_id = post.get("id", md_file.stem)
            name = post.get("name", team_id)
            description = post.get("description", "")
            category = post.get("category", "general")

            try:
                content, files = collector.collect(team_id, "team")
            except FileNotFoundError:
                content = frontmatter.dumps(post)
                files = {}

            teams.append({
                "store_name": team_id,
                "display_name": name,
                "description": description.strip() if isinstance(description, str) else str(description).strip(),
                "category": category,
                "tags": [],
                "content": content,
                "files": files,
                "source": "factory",
            })

        return teams

    # ------------------------------------------------------------------ #
    #  External discovery                                                  #
    # ------------------------------------------------------------------ #

    def _clone_repo(self, url: str) -> Tuple[Path, dict]:
        """Clone a repo to a temp directory.

        Returns (tmp_dir, source_meta) where source_meta records the upstream
        commit we cloned: {"source_rev", "source_committed_at"}.
        """
        import os
        import subprocess
        tmp_dir = Path(tempfile.mkdtemp(prefix="pantheon_seed_"))
        console.print(f"  Cloning {url} ...")
        env = os.environ.copy()
        env["GIT_LFS_SKIP_SMUDGE"] = "1"  # Skip LFS files
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(tmp_dir)],
            check=True, capture_output=True, env=env,
        )
        rev, committed = _git_meta(tmp_dir)
        return tmp_dir, {"source_rev": rev, "source_committed_at": committed}

    def _convert_external_skill(self, skill_md_path: Path, source_name: str,
                                 source_config: dict,
                                 category_hint: Optional[str] = None) -> Optional[dict]:
        """Convert an external SKILL.md to Pantheon format.

        Also bundles sibling .md files from the same directory into `files`.
        """
        try:
            post = frontmatter.load(str(skill_md_path))
        except Exception as e:
            logger.debug(f"Failed to parse {skill_md_path}: {e}")
            return None

        raw_name = post.get("name", skill_md_path.parent.name)
        if not raw_name:
            return None

        skill_id = _slugify(str(raw_name))
        if not skill_id:
            return None

        display_name = str(raw_name).replace("-", " ").replace("_", " ").title()
        description = post.get("description", "")
        if isinstance(description, str):
            description = description.strip()
        else:
            description = str(description).strip()

        license_info = post.get("license", "")

        # Determine category
        if category_hint:
            category = category_hint
        else:
            fm_category = post.get("category", "")
            if fm_category:
                category = _slugify(str(fm_category))
            else:
                category = "general"

        tags = post.get("tags", []) or []
        if not isinstance(tags, list):
            tags = []

        # Build new frontmatter
        new_meta = {
            "id": skill_id,
            "name": display_name,
            "description": description,
            "tags": tags,
            "source": source_name,
            "source_url": source_config["source_url"],
        }

        # Build attribution header
        source_display = source_config["display_name"]
        source_url = source_config["source_url"]
        attribution = f"> **Source**: [{source_display}]({source_url})"
        if license_info and license_info != "Unknown":
            attribution += f" | License: {license_info}"
        attribution += "\n"

        original_content = post.content.strip()
        new_content = f"{attribution}\n{original_content}"
        new_post = frontmatter.Post(new_content, **new_meta)

        store_name = f"{source_name}_{skill_id}"

        # Bundle all files in the skill directory recursively (code, data, etc.)
        files: Dict[str, str] = {}
        skill_dir = skill_md_path.parent
        for child in sorted(skill_dir.rglob("*")):
            if not child.is_file():
                continue
            if child.name == "SKILL.md":
                continue
            # Skip hidden files, __pycache__, tests
            rel_to_skill = child.relative_to(skill_dir)
            parts = rel_to_skill.parts
            if any(p.startswith(".") or p == "__pycache__" for p in parts):
                continue
            try:
                file_content = child.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue
            file_key = f"skills/{store_name}/{str(rel_to_skill).replace(chr(92), '/')}"
            files[file_key] = file_content

        return {
            "store_name": store_name,
            "display_name": display_name,
            "description": description[:500],
            "category": category,
            "tags": tags,
            "content": frontmatter.dumps(new_post),
            "files": files,
            "source": source_name,
        }

    def _discover_external_skills(self, repo_path: Path, source_name: str,
                                   source_config: dict):
        """Discover all SKILL.md files in an external repo."""
        skills_dir = repo_path / source_config["skills_dir"]
        if not skills_dir.exists():
            console.print(f"  [red]Skills directory not found: {skills_dir}[/red]")
            return []

        skills = []
        has_categories = source_config.get("has_categories", False)

        for skill_md in sorted(skills_dir.rglob("SKILL.md")):
            rel = skill_md.relative_to(skills_dir)
            parts = rel.parts

            category_hint = None
            if has_categories and len(parts) >= 3:
                cat_dir = parts[0]
                if source_name == "labclaw":
                    category_hint = LABCLAW_CATEGORY.get(cat_dir, cat_dir)
                else:
                    category_hint = cat_dir

            converted = self._convert_external_skill(
                skill_md, source_name, source_config, category_hint
            )
            if converted:
                converted["_dirname"] = skill_md.parent.name
                skills.append(converted)

        # Resolve "Related Skills" names (= sibling directory names) to store names
        by_dirname = {_slugify(s["_dirname"]): s["store_name"] for s in skills}
        for s in skills:
            refs: List[str] = []
            _rel_paths, names = _extract_refs(s["content"])
            for nm in names:
                r = by_dirname.get(_slugify(nm))
                if r and r != s["store_name"]:
                    refs.append(r)
            s["references"] = list(dict.fromkeys(refs))
            s.pop("_dirname", None)

        return _dedup_by_store_name(skills, source_name)

    # ------------------------------------------------------------------ #
    #  prepare: Collect everything into a local directory                   #
    # ------------------------------------------------------------------ #

    def prepare(self, output_dir: str = "store_seed_data"):
        """Collect all packages into a local directory with manifest.json.

        Output structure:
            {output_dir}/
                manifest.json          # Index of all packages
                skills/
                    factory/
                        omics_quality_control/SKILL.md
                        ...
                    labclaw/
                        labclaw_scanpy/SKILL.md
                        ...
                agents/
                    researcher.md
                    ...
                teams/
                    default.md
                    ...
        """
        out = Path(output_dir)
        manifest = []

        # Pantheon's own (factory) content lives in this repo — record its HEAD so
        # the store can tell how fresh the Pantheon-authored packages are too.
        factory_dir = Path(__file__).parent.parent / "factory" / "templates" / "skills"
        factory_rev, factory_committed = _git_meta(factory_dir)

        # --- Factory skills ---
        skills = self._discover_factory_skills()
        skills_dir = out / "skills" / "factory"
        skills_dir.mkdir(parents=True, exist_ok=True)
        console.print(f"\n[bold]Factory Skills[/bold]: {len(skills)}")
        for skill in skills:
            # Always use {name}/SKILL.md directory format
            skill_out_dir = skills_dir / skill["store_name"]
            skill_out_dir.mkdir(parents=True, exist_ok=True)
            fpath = skill_out_dir / "SKILL.md"
            fpath.write_text(skill["content"], encoding="utf-8")
            entry = {
                "name": skill["store_name"],
                "type": "skill",
                "display_name": skill["display_name"],
                "description": skill["description"],
                "category": skill["category"],
                "tags": skill.get("tags", []),
                "references": skill.get("references", []),
                "source": "Pantheon",
                "source_url": None,
                "source_rev": factory_rev,
                "source_committed_at": factory_committed,
                "file": str(fpath.relative_to(out)).replace("\\", "/"),
            }
            # Save bundled skill files
            files = skill.get("files", {})
            if files:
                bundled_files = {}
                for rel_path, content in files.items():
                    bf_name = hashlib.md5(rel_path.encode()).hexdigest()[:12] + Path(rel_path).suffix
                    bf = skill_out_dir / bf_name
                    bf.write_text(content, encoding="utf-8")
                    bundled_files[rel_path] = str(bf.relative_to(out)).replace("\\", "/")
                entry["bundled_files"] = bundled_files
            manifest.append(entry)

        # --- Factory agents ---
        agents = self._discover_factory_agents()
        agents_dir = out / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        console.print(f"[bold]Factory Agents[/bold]: {len(agents)}")
        for agent in agents:
            fpath = agents_dir / f"{agent['store_name']}.md"
            fpath.write_text(agent["content"], encoding="utf-8")
            manifest.append({
                "name": agent["store_name"],
                "type": "agent",
                "display_name": agent["display_name"],
                "description": agent["description"],
                "category": agent["category"],
                "tags": [],
                "source": "Pantheon",
                "source_url": None,
                "source_rev": factory_rev,
                "source_committed_at": factory_committed,
                "file": str(fpath.relative_to(out)).replace("\\", "/"),
            })

        # --- Factory teams ---
        teams = self._discover_factory_teams()
        teams_dir = out / "teams"
        teams_dir.mkdir(parents=True, exist_ok=True)
        console.print(f"[bold]Factory Teams[/bold]: {len(teams)}")
        for team in teams:
            fpath = teams_dir / f"{team['store_name']}.md"
            fpath.write_text(team["content"], encoding="utf-8")
            entry = {
                "name": team["store_name"],
                "type": "team",
                "display_name": team["display_name"],
                "description": team["description"],
                "category": team["category"],
                "tags": [],
                "source": "Pantheon",
                "source_url": None,
                "source_rev": factory_rev,
                "source_committed_at": factory_committed,
                "file": str(fpath.relative_to(out)).replace("\\", "/"),
            }
            # Save bundled agent files
            files = team.get("files", {})
            if files:
                bundled_dir = teams_dir / f"{team['store_name']}_bundled"
                bundled_dir.mkdir(parents=True, exist_ok=True)
                bundled_files = {}
                for rel_path, content in files.items():
                    bf_name = hashlib.md5(rel_path.encode()).hexdigest()[:12] + Path(rel_path).suffix
                    bf = bundled_dir / bf_name
                    bf.write_text(content, encoding="utf-8")
                    bundled_files[rel_path] = str(bf.relative_to(out)).replace("\\", "/")
                entry["bundled_files"] = bundled_files
            manifest.append(entry)

        # --- External repos ---
        for source_name, config in EXTERNAL_REPOS.items():
            console.print(f"\n[bold]Cloning {config['display_name']}[/bold]...")
            try:
                repo_path, src_meta = self._clone_repo(config["url"])
            except Exception as e:
                console.print(f"  [red]Failed to clone: {e}[/red]")
                continue
            if src_meta.get("source_rev"):
                console.print(f"  [dim]at {src_meta['source_rev'][:10]} "
                              f"({src_meta.get('source_committed_at') or '?'})[/dim]")

            ext_skills = self._discover_external_skills(repo_path, source_name, config)
            ext_dir = out / "skills" / source_name
            ext_dir.mkdir(parents=True, exist_ok=True)
            console.print(f"  Found {len(ext_skills)} skills")

            with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                           BarColumn(), TextColumn("{task.completed}/{task.total}"),
                           console=console) as progress:
                task = progress.add_task(f"Saving {source_name}...", total=len(ext_skills))
                for skill in ext_skills:
                    # Always use {name}/SKILL.md directory format
                    skill_out_dir = ext_dir / skill["store_name"]
                    skill_out_dir.mkdir(parents=True, exist_ok=True)
                    fpath = skill_out_dir / "SKILL.md"
                    fpath.write_text(skill["content"], encoding="utf-8")
                    entry = {
                        "name": skill["store_name"],
                        "type": "skill",
                        "display_name": skill["display_name"],
                        "description": skill["description"],
                        "category": skill["category"],
                        "tags": skill.get("tags", []),
                        "references": skill.get("references", []),
                        "source": config["display_name"],
                        "source_url": config["source_url"],
                        "source_rev": src_meta.get("source_rev"),
                        "source_committed_at": src_meta.get("source_committed_at"),
                        "file": str(fpath.relative_to(out)).replace("\\", "/"),
                    }
                    # Save bundled skill files
                    files = skill.get("files", {})
                    if files:
                        bundled_files = {}
                        for rel_path, content in files.items():
                            bf_name = hashlib.md5(rel_path.encode()).hexdigest()[:12] + Path(rel_path).suffix
                            bf = skill_out_dir / bf_name
                            bf.write_text(content, encoding="utf-8")
                            bundled_files[rel_path] = str(bf.relative_to(out)).replace("\\", "/")
                        entry["bundled_files"] = bundled_files
                    manifest.append(entry)
                    progress.advance(task)

            # Cleanup cloned repo
            import shutil
            shutil.rmtree(repo_path, ignore_errors=True)

        # --- Write manifest ---
        manifest_path = out / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # --- Summary ---
        by_type = {}
        by_source = {}
        for entry in manifest:
            t = entry["type"]
            s = entry["source"]
            by_type[t] = by_type.get(t, 0) + 1
            by_source[s] = by_source.get(s, 0) + 1

        table = Table(title=f"Prepared {len(manifest)} packages -> {out}")
        table.add_column("Type/Source", style="bold")
        table.add_column("Count", justify="right")
        for t, c in sorted(by_type.items()):
            table.add_row(f"[cyan]{t}[/cyan]", str(c))
        table.add_row("", "")
        for s, c in sorted(by_source.items()):
            table.add_row(f"[green]{s}[/green]", str(c))
        console.print(table)
        console.print(f"\nManifest: [bold]{manifest_path}[/bold]")

    # ------------------------------------------------------------------ #
    #  publish: Read from prepared directory and publish to Hub             #
    # ------------------------------------------------------------------ #

    def publish_prepared(self, input_dir: str = "store_seed_data",
                         dry_run: bool = False):
        """Read manifest.json from a prepared directory and publish all to Hub.

        Args:
            input_dir: Path to the prepared directory (output of `prepare`)
            dry_run: Preview without publishing
        """
        self._reset_stats()
        inp = Path(input_dir)
        manifest_path = inp / "manifest.json"

        if not manifest_path.exists():
            raise SystemExit(f"manifest.json not found in {inp}")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        console.print(f"\n[bold]Publishing from {inp}[/bold] ({len(manifest)} packages)")

        with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                       BarColumn(), TextColumn("{task.completed}/{task.total}"),
                       console=console) as progress:
            task = progress.add_task("Publishing...", total=len(manifest))
            for entry in manifest:
                file_path = inp / entry["file"]
                if not file_path.exists():
                    logger.warning(f"File not found: {file_path}")
                    self.stats["failed"] += 1
                    progress.advance(task)
                    continue

                content = file_path.read_text(encoding="utf-8")

                # Load bundled files (teams bundle agents, skill groups bundle sub-skills)
                files = {}
                if entry.get("bundled_files"):
                    for rel_path, bf_rel in entry["bundled_files"].items():
                        bf_path = inp / bf_rel
                        if bf_path.exists():
                            files[rel_path] = bf_path.read_text(encoding="utf-8")

                self._publish_one(
                    name=entry["name"],
                    pkg_type=entry["type"],
                    display_name=entry["display_name"],
                    description=entry["description"],
                    category=entry["category"],
                    content=content,
                    files=files,
                    source=entry.get("source", "Pantheon"),
                    source_url=entry.get("source_url"),
                    references=entry.get("references", []),
                    source_rev=entry.get("source_rev"),
                    source_committed_at=entry.get("source_committed_at"),
                    dry_run=dry_run,
                )
                progress.advance(task)
                if not dry_run:
                    time.sleep(0.2)

        self._print_summary("Publish from prepared data")

        # Reconcile each source: a skill that vanished from upstream (renamed or
        # removed) is still in the store. If it carries reviews, either reclaim
        # them onto the renamed package (same content, new name) or warn that
        # they would be stranded.
        if not dry_run:
            by_source_names: Dict[str, set] = {}
            for entry in manifest:
                by_source_names.setdefault(entry.get("source", "Pantheon"), set()).add(entry["name"])
            console.print("\n[bold]Reconciling sources (stranded reviews)[/bold]...")
            for source_display, names in by_source_names.items():
                try:
                    self._reconcile_source(source_display, names)
                except Exception as e:
                    logger.warning(f"reconcile {source_display} failed: {e}")

    def _publish_one(self, name: str, pkg_type: str, display_name: str,
                     description: str, category: str, content: str,
                     files: dict = None, version: str = "1.0.0",
                     source: str = "Pantheon", source_url: str = None,
                     references: list = None, source_rev: str = None,
                     source_committed_at: str = None, dry_run: bool = False) -> bool:
        """Publish a package. If it already exists, publish a NEW VERSION when the
        content changed (auto-bumped patch), else skip. This makes re-seeding a
        real content sync instead of a no-op, with version history."""
        if dry_run:
            console.print(f"  [dim][dry-run][/dim] {pkg_type}: {name} ({category})")
            self.stats["published"] += 1
            return True

        payload = {
            "name": name, "type": pkg_type, "display_name": display_name,
            "description": description or "", "category": category, "version": version,
            "content": content, "files": files or {}, "source": source,
            "references": references or [],
        }
        if source_url:
            payload["source_url"] = source_url
        if source_rev:
            payload["source_rev"] = source_rev
        if source_committed_at:
            payload["source_committed_at"] = source_committed_at

        # 1. Try to create as a brand-new package.
        try:
            _run(self.client.publish(payload))
            self.stats["published"] += 1
            return True
        except SystemExit:
            pass  # already exists -> fall through to the version-update path
        except Exception as e:
            logger.warning(f"Failed to publish {name}: {e}")
            self.stats["failed"] += 1
            return False

        # 2. Exists: look it up, and publish a new version if the content changed.
        try:
            res = _run(self.client.search(q=name, limit=8))
            existing = next((p for p in res.get("packages", []) if p.get("name") == name), None)
            if not existing:
                self.stats["skipped"] += 1
                return False
            pkg_id = existing["id"]
            next_ver = _bump_patch(existing.get("latest_version") or "1.0.0")
            try:
                _run(self.client.publish_version(pkg_id, {
                    "version": next_ver, "content": content, "files": files or {},
                    "changelog": "Synced from source",
                }))
            except SystemExit:
                # content identical (or version clash) -> no update needed
                self.stats["skipped"] += 1
                return False
            # version published -> sync package-level metadata (refs/desc/tags/source rev)
            try:
                meta_update = {
                    "references": references or [], "description": description or "",
                    "display_name": display_name, "category": category,
                }
                if source_rev:
                    meta_update["source_rev"] = source_rev
                if source_committed_at:
                    meta_update["source_committed_at"] = source_committed_at
                _run(self.client.update_package(pkg_id, meta_update))
            except Exception:
                pass  # version is published; metadata sync is best-effort
            self.stats["updated"] += 1
            return True
        except Exception as e:
            logger.warning(f"Failed to update {name}: {e}")
            self.stats["failed"] += 1
            return False

    def _print_summary(self, title: str):
        """Print a summary table."""
        table = Table(title=title)
        table.add_column("Status", style="bold")
        table.add_column("Count", justify="right")
        table.add_row("[green]Published (new)[/green]", str(self.stats["published"]))
        table.add_row("[cyan]Updated (new version)[/cyan]", str(self.stats["updated"]))
        table.add_row("[yellow]Skipped (unchanged)[/yellow]", str(self.stats["skipped"]))
        table.add_row("[red]Failed[/red]", str(self.stats["failed"]))
        console.print(table)

    # ------------------------------------------------------------------ #
    #  Reconciliation: detect renamed/removed upstream skills              #
    # ------------------------------------------------------------------ #

    def _all_hub_packages_for_source(self, source_display: str) -> list:
        """Page through every store package that belongs to `source_display`."""
        out, offset, page = [], 0, 100
        while True:
            res = _run(self.client.search(source=source_display, limit=page, offset=offset))
            pkgs = res.get("packages", [])
            out.extend(pkgs)
            if len(pkgs) < page:
                break
            offset += page
        return out

    def _reconcile_source(self, source_display: str, manifest_names: set):
        """For one source, compare what the store holds against what upstream
        still provides. A package present in the store but absent from this seed
        run is an orphan (upstream renamed or removed it). Orphans carrying reviews
        are either reclaimed (content matches a current package -> rename) or warned
        about (content gone -> reviews would be stranded)."""
        hub_pkgs = self._all_hub_packages_for_source(source_display)
        if not hub_pkgs:
            return
        live_by_hash = {
            p["content_hash"]: p for p in hub_pkgs
            if p["name"] in manifest_names and p.get("content_hash")
        }
        orphans = [p for p in hub_pkgs if p["name"] not in manifest_names]
        for orphan in orphans:
            rc = orphan.get("rating_count", 0) or 0
            if rc == 0:
                continue  # no reviews -> nothing to lose, leave it as a stale package
            match = live_by_hash.get(orphan.get("content_hash"))
            if match and match["name"] != orphan["name"]:
                try:
                    _run(self.client.transfer_reviews(match["id"], orphan["id"]))
                    console.print(
                        f"  [green]reclaimed[/green] {rc} review(s): "
                        f"{orphan['name']} -> {match['name']} (renamed upstream)"
                    )
                except Exception as e:
                    console.print(f"  [yellow]reclaim failed[/yellow] "
                                  f"{orphan['name']} -> {match['name']}: {e}")
            else:
                console.print(
                    f"  [yellow]⚠ stranded[/yellow]: '{orphan['name']}' has {rc} review(s) "
                    f"but is gone from upstream (no content match). If it was renamed, run:\n"
                    f"      pantheon store seed reclaim-reviews "
                    f"--from-name {orphan['name']} --to-name <new-name>"
                )

    def reclaim_reviews(self, from_name: str, to_name: str):
        """Manually move reviews from one package to another (rename recovery).

        Usage: pantheon store seed reclaim-reviews --from-name old --to-name new
        """
        if not self.client.auth.is_logged_in:
            raise SystemExit("Not logged in. Run: pantheon store login")
        res = _run(self.client.transfer_reviews(to_name, from_name))
        console.print(f"[green]Moved {res.get('moved', 0)} review(s)[/green] "
                      f"{res.get('from')} -> {res.get('to')}")

    def check_updates(self):
        """Compare each external source's live upstream HEAD against the revision
        the store currently holds, so you can see what has drifted out of date.

        Usage: pantheon store seed check-updates
        """
        import subprocess
        table = Table(title="Source freshness (store vs upstream HEAD)")
        table.add_column("Source", style="bold")
        table.add_column("Store rev")
        table.add_column("Upstream HEAD")
        table.add_column("Status")
        for source_name, config in EXTERNAL_REPOS.items():
            display, url = config["display_name"], config["url"]
            try:
                out = subprocess.run(["git", "ls-remote", url, "HEAD"],
                                     capture_output=True, text=True, check=True).stdout
                live = out.split()[0] if out.strip() else None
            except Exception:
                live = None
            try:
                res = _run(self.client.search(source=display, limit=1))
                pkgs = res.get("packages", [])
                held = pkgs[0].get("source_rev") if pkgs else None
            except Exception:
                held = None
            if not held:
                status = "[dim]no source_rev — re-seed to record[/dim]"
            elif not live:
                status = "[red]upstream unreachable[/red]"
            elif held == live:
                status = "[green]up to date[/green]"
            else:
                status = "[yellow]BEHIND — upstream moved[/yellow]"
            table.add_row(display, (held or "—")[:10], (live or "—")[:10], status)
        console.print(table)
