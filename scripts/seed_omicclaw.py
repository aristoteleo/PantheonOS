"""Incremental seed: prepare and publish only omicclaw skills."""
import sys
import json
import hashlib
import time
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pantheonos.store.seed import StoreSeed, EXTERNAL_REPOS

seed = StoreSeed()

# Only process omicclaw
config = EXTERNAL_REPOS["omicclaw"]
print(f"Cloning {config['display_name']}...")
repo_path = seed._clone_repo(config["url"])

skills = seed._discover_external_skills(repo_path, "omicclaw", config)
print(f"Found {len(skills)} skills")

# Prepare to output dir
out = Path("store_seed_data")
ext_dir = out / "skills" / "omicclaw"
ext_dir.mkdir(parents=True, exist_ok=True)

# Build manifest entries for omicclaw only
omicclaw_manifest = []
for skill in skills:
    # Use {name}/SKILL.md directory format
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
        "source": config["display_name"],
        "source_url": config["source_url"],
        "file": str(fpath.relative_to(out)).replace("\\", "/"),
    }
    files = skill.get("files", {})
    if files:
        bundled_files = {}
        for rel_path, content in files.items():
            bf_name = hashlib.md5(rel_path.encode()).hexdigest()[:12] + Path(rel_path).suffix
            bf = skill_out_dir / bf_name
            bf.write_text(content, encoding="utf-8")
            bundled_files[rel_path] = str(bf.relative_to(out)).replace("\\", "/")
        entry["bundled_files"] = bundled_files
    omicclaw_manifest.append(entry)
    print(f"  Prepared: {skill['store_name']} ({skill['category']})")

# Write omicclaw-only manifest
manifest_path = out / "manifest_omicclaw.json"
manifest_path.write_text(
    json.dumps(omicclaw_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
)
print(f"\nPrepared {len(omicclaw_manifest)} omicclaw skills -> {manifest_path}")

# Cleanup cloned repo
shutil.rmtree(repo_path, ignore_errors=True)

# Now publish
print("\nPublishing to Hub...")
seed._reset_stats()
for entry in omicclaw_manifest:
    file_path = out / entry["file"]
    content = file_path.read_text(encoding="utf-8")
    files = {}
    if entry.get("bundled_files"):
        for rel_path, bf_rel in entry["bundled_files"].items():
            bf_path = out / bf_rel
            if bf_path.exists():
                files[rel_path] = bf_path.read_text(encoding="utf-8")
    result = seed._publish_one(
        name=entry["name"],
        pkg_type=entry["type"],
        display_name=entry["display_name"],
        description=entry["description"],
        category=entry["category"],
        content=content,
        files=files,
        source=entry.get("source", "Pantheon"),
        source_url=entry.get("source_url"),
    )
    status = "published" if result else "skipped/failed"
    print(f"  {status}: {entry['name']}")
    time.sleep(0.2)

seed._print_summary("OmicClaw incremental publish")
