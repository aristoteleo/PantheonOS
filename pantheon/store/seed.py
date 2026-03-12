"""Batch seed the Pantheon Store with factory and external skills.

Two modes:
  - prepare: Collect all packages into a local directory + manifest.json
  - publish: Read from prepared directory and batch-publish to Hub API
"""

import asyncio
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

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
}


def _slugify(name: str) -> str:
    """Convert a name to a valid skill ID slug."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\-_]", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


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
        self.stats = {"published": 0, "skipped": 0, "failed": 0}

    def _reset_stats(self):
        self.stats = {"published": 0, "skipped": 0, "failed": 0}

    # ------------------------------------------------------------------ #
    #  Factory discovery                                                   #
    # ------------------------------------------------------------------ #

    def _discover_factory_skills(self):
        """Discover all publishable skill files in factory/templates/skills/."""
        factory_dir = Path(__file__).parent.parent / "factory" / "templates" / "skills"
        skills = []

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
            })

        return skills

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

    def _clone_repo(self, url: str) -> Path:
        """Clone a repo to a temp directory."""
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
        return tmp_dir

    def _convert_external_skill(self, skill_md_path: Path, source_name: str,
                                 source_config: dict,
                                 category_hint: Optional[str] = None) -> Optional[dict]:
        """Convert an external SKILL.md to Pantheon format."""
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

        return {
            "store_name": store_name,
            "display_name": display_name,
            "description": description[:500],
            "category": category,
            "tags": tags,
            "content": frontmatter.dumps(new_post),
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
                skills.append(converted)

        return skills

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
                        omics_quality_control.md
                        ...
                    labclaw/
                        labclaw_scanpy.md
                        ...
                    openclaw-medical/
                        ...
                    claude-scientific/
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

        # --- Factory skills ---
        skills = self._discover_factory_skills()
        skills_dir = out / "skills" / "factory"
        skills_dir.mkdir(parents=True, exist_ok=True)
        console.print(f"\n[bold]Factory Skills[/bold]: {len(skills)}")
        for skill in skills:
            fpath = skills_dir / f"{skill['store_name']}.md"
            fpath.write_text(skill["content"], encoding="utf-8")
            manifest.append({
                "name": skill["store_name"],
                "type": "skill",
                "display_name": skill["display_name"],
                "description": skill["description"],
                "category": skill["category"],
                "tags": skill.get("tags", []),
                "source": "factory",
                "file": str(fpath.relative_to(out)).replace("\\", "/"),
            })

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
                "source": "factory",
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
                "source": "factory",
                "file": str(fpath.relative_to(out)).replace("\\", "/"),
            }
            # Save bundled agent files
            files = team.get("files", {})
            if files:
                bundled_dir = teams_dir / f"{team['store_name']}_bundled"
                bundled_dir.mkdir(parents=True, exist_ok=True)
                bundled_files = {}
                for rel_path, content in files.items():
                    bf = bundled_dir / rel_path.replace("/", "_")
                    bf.write_text(content, encoding="utf-8")
                    bundled_files[rel_path] = str(bf.relative_to(out)).replace("\\", "/")
                entry["bundled_files"] = bundled_files
            manifest.append(entry)

        # --- External repos ---
        for source_name, config in EXTERNAL_REPOS.items():
            console.print(f"\n[bold]Cloning {config['display_name']}[/bold]...")
            try:
                repo_path = self._clone_repo(config["url"])
            except Exception as e:
                console.print(f"  [red]Failed to clone: {e}[/red]")
                continue

            ext_skills = self._discover_external_skills(repo_path, source_name, config)
            ext_dir = out / "skills" / source_name
            ext_dir.mkdir(parents=True, exist_ok=True)
            console.print(f"  Found {len(ext_skills)} skills")

            with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                           BarColumn(), TextColumn("{task.completed}/{task.total}"),
                           console=console) as progress:
                task = progress.add_task(f"Saving {source_name}...", total=len(ext_skills))
                for skill in ext_skills:
                    fpath = ext_dir / f"{skill['store_name']}.md"
                    fpath.write_text(skill["content"], encoding="utf-8")
                    manifest.append({
                        "name": skill["store_name"],
                        "type": "skill",
                        "display_name": skill["display_name"],
                        "description": skill["description"],
                        "category": skill["category"],
                        "tags": skill.get("tags", []),
                        "source": source_name,
                        "file": str(fpath.relative_to(out)).replace("\\", "/"),
                    })
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

                # Load bundled files for teams
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
                    dry_run=dry_run,
                )
                progress.advance(task)
                if not dry_run:
                    time.sleep(0.2)

        self._print_summary("Publish from prepared data")

    def _publish_one(self, name: str, pkg_type: str, display_name: str,
                     description: str, category: str, content: str,
                     files: dict = None, version: str = "1.0.0",
                     dry_run: bool = False) -> bool:
        """Publish a single package. Returns True if published."""
        if dry_run:
            console.print(f"  [dim][dry-run][/dim] {pkg_type}: {name} ({category})")
            self.stats["published"] += 1
            return True

        try:
            _run(self.client.publish({
                "name": name,
                "type": pkg_type,
                "display_name": display_name,
                "description": description or "",
                "category": category,
                "version": version,
                "content": content,
                "files": files or {},
            }))
            self.stats["published"] += 1
            return True
        except SystemExit:
            logger.debug(f"Skipped (exists): {name}")
            self.stats["skipped"] += 1
            return False
        except Exception as e:
            logger.warning(f"Failed to publish {name}: {e}")
            self.stats["failed"] += 1
            return False

    def _print_summary(self, title: str):
        """Print a summary table."""
        table = Table(title=title)
        table.add_column("Status", style="bold")
        table.add_column("Count", justify="right")
        table.add_row("[green]Published[/green]", str(self.stats["published"]))
        table.add_row("[yellow]Skipped (exists)[/yellow]", str(self.stats["skipped"]))
        table.add_row("[red]Failed[/red]", str(self.stats["failed"]))
        console.print(table)
