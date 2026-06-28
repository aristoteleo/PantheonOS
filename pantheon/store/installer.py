"""Package installer for downloading and installing Store packages locally."""

from pathlib import Path
from typing import Dict, Optional

from loguru import logger


def _skill_install_root(name: str) -> str:
    """Relative install root for a skill: ``skills/<source-slug>/<base>``.

    External store names are ``<source-slug>_<base>`` (e.g.
    ``bioskills_bio-clinical-...``). Foldering by source drops the prefix and
    encodes provenance in the path. Names without a ``_`` fall back to
    ``skills/<name>`` unchanged.
    """
    if "_" in name:
        slug, base = name.split("_", 1)
        if slug and base:
            return f"skills/{slug}/{base}"
    return f"skills/{name}"


class PackageInstaller:
    """Install/uninstall agent, team, and skill packages from the Store.

    Packages are installed to the user's ~/.pantheon/ directory structure:
      - agents/{name}.md
      - teams/{name}.md  (+ bundled agents)
      - skills/{name}/SKILL.md  (+ bundled files)
    """

    def __init__(self, work_dir: Optional[Path] = None):
        from pantheon.settings import get_settings
        self.settings = get_settings(work_dir)

    def install(
        self,
        pkg_type: str,
        name: str,
        content: str,
        files: Optional[Dict[str, str]] = None,
        path: Optional[str] = None,
    ) -> list[Path]:
        """Install a package locally.

        Args:
            pkg_type: One of "agent", "team", "skill".
            name: Package name (used as filename).
            content: Main .md file content.
            files: Optional dict of relative_path -> content for bundled files
                   (e.g., {"agents/researcher.md": "..."} for team packages).

        Returns:
            List of paths that were written.
        """
        written: list[Path] = []

        if pkg_type == "agent":
            target = self.settings.agents_dir / f"{name}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(target)

        elif pkg_type == "team":
            target = self.settings.teams_dir / f"{name}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(target)

            # Write bundled agent files
            if files:
                for rel_path, file_content in files.items():
                    # rel_path is like "agents/researcher.md"
                    file_target = self.settings.pantheon_dir / rel_path
                    file_target.parent.mkdir(parents=True, exist_ok=True)
                    file_target.write_text(file_content, encoding="utf-8")
                    written.append(file_target)

        elif pkg_type == "skill":
            # Install under a SOURCE folder with a clean (un-prefixed) base name:
            #   skills/<source-slug>/<base>/SKILL.md
            # External store names are "<source-slug>_<base>" (e.g.
            # "bioskills_bio-clinical-..."). Foldering by source keeps names clean,
            # mirrors the factory layout, and encodes provenance in the path instead
            # of a prefix. (Full original nesting can't be reconstructed until the
            # store stores the source path; this is the source-folder layout.)
            # Prefer the original hierarchical source path (restores the exact repo
            # layout, e.g. skills/omics/database_access/gget). Fall back to the
            # source-folder layout when the store didn't carry a path.
            rel_root = f"skills/{path.strip('/')}" if path else _skill_install_root(name)
            target = self.settings.pantheon_dir / rel_root / "SKILL.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(target)

            # Write bundled skill files, rewriting the old flat "skills/{name}/..."
            # prefix onto the new source-folder root.
            if files:
                old_prefix = f"skills/{name}/"
                for rel_path, file_content in files.items():
                    rp = rel_path
                    if rp.startswith(old_prefix):
                        rp = f"{rel_root}/" + rp[len(old_prefix):]
                    file_target = self.settings.pantheon_dir / rp
                    file_target.parent.mkdir(parents=True, exist_ok=True)
                    file_target.write_text(file_content, encoding="utf-8")
                    written.append(file_target)

        else:
            raise ValueError(f"Unknown package type: {pkg_type}")

        for p in written:
            logger.info(f"Installed: {p}")

        return written

    def uninstall(self, pkg_type: str, name: str, path: Optional[str] = None) -> list[Path]:
        """Uninstall a package by removing its files.

        Args:
            pkg_type: One of "agent", "team", "skill".
            name: Package name.
            path: Original hierarchical source path the skill was installed under
                  (e.g. "omics/database_access/gget"). When recorded at install
                  time, it pins the exact dir to remove.

        Returns:
            List of paths that were removed.
        """
        removed: list[Path] = []

        if pkg_type == "agent":
            target = self.settings.agents_dir / f"{name}.md"
            if target.exists():
                target.unlink()
                removed.append(target)

        elif pkg_type == "team":
            target = self.settings.teams_dir / f"{name}.md"
            if target.exists():
                target.unlink()
                removed.append(target)
            # Note: bundled agents are NOT removed automatically
            # as they may be shared with other teams.

        elif pkg_type == "skill":
            # Try the recorded original path first (matches the path-based install
            # layout), then the source-folder layout, then the legacy flat-prefixed
            # dir, then the legacy single-file form.
            candidates = []
            if path:
                candidates.append(self.settings.pantheon_dir / "skills" / path.strip("/"))
            candidates += [
                self.settings.pantheon_dir / _skill_install_root(name),  # skills/<slug>/<base>
                self.settings.skills_dir / name,                          # legacy skills/<name>
            ]
            removed_dir = False
            for dir_target in candidates:
                if dir_target.is_dir():
                    import shutil
                    removed_files = list(dir_target.rglob("*"))
                    shutil.rmtree(dir_target)
                    removed.extend(removed_files)
                    removed.append(dir_target)
                    removed_dir = True
                    break
            if not removed_dir:
                flat_target = self.settings.skills_dir / f"{name}.md"
                if flat_target.exists():
                    flat_target.unlink()
                    removed.append(flat_target)

        else:
            raise ValueError(f"Unknown package type: {pkg_type}")

        for p in removed:
            logger.info(f"Removed: {p}")

        if not removed:
            logger.warning(f"No files found to remove for {pkg_type}/{name}")

        return removed
