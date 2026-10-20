# Pantheon Store Seed Guide

How to initialize (seed) the Pantheon Store with packages from factory templates and external repositories.

## Prerequisites

1. **Install pantheon-agents** (editable mode recommended):
   ```bash
   cd pantheon-agents
   pip install -e .
   ```

2. **A running Pantheon Hub** with store API enabled:
   ```bash
   # Dev mode (in pantheon-hub repo):
   cd pantheon-hub
   uvicorn scripts.run_store_dev:app --reload --port 8000
   ```
   Default production Hub: `https://pantheon.aristoteleo.com`

3. **A Hub account** - Register at https://app.pantheonos.stanford.edu/

---

## Quick Start (Two-Step Workflow)

### Step 1: Prepare seed data

Collect all packages (factory + external repos) into a local directory:

```bash
pantheon store seed prepare
```

This will:
- Discover factory skills/agents/teams from `apps/agent/pantheon/factory/templates/`
- Clone and scan external repos (LabClaw, OpenClaw Medical, Claude Scientific, ClawBio)
- Write all files + `manifest.json` to `store_seed_data/`

### Step 2: Login & publish

```bash
# Login first
pantheon store login
# Enter your username and password when prompted

# Dry run (preview what will be published)
pantheon store seed publish --dry-run

# Publish for real
pantheon store seed publish
```

That's it! All packages are now in the Store.

---

## Detailed Command Reference

### Authentication

```bash
# Login (prompts for credentials)
pantheon store login
pantheon store login --hub-url https://your-hub.com

# Logout
pantheon store logout
```

Credentials are stored in `~/.pantheon/store_auth.json`.

### Seed Commands

```bash
# Prepare: collect all packages into store_seed_data/
pantheon store seed prepare
pantheon store seed prepare --output-dir my_seed_data

# Publish: batch upload from prepared directory
pantheon store seed publish
pantheon store seed publish --hub-url http://localhost:8000
pantheon store seed publish --dry-run    # preview only
```

### Individual Package Operations

```bash
# Publish a single package
pantheon store publish my_skill --type skill
pantheon store publish my_agent --type agent
pantheon store publish my_team  --type team

# Search
pantheon store search "cell type"
pantheon store search --type skill --category single-cell

# Install / Uninstall
pantheon store install package_name
pantheon store install package_name --version 1.0.0
pantheon store uninstall package_name --type skill

# Info
pantheon store info package_name
pantheon store list --what published    # your published packages
pantheon store list --what installed    # your installed packages
```

---

## Seed Data Structure

After `pantheon store seed prepare`, the output looks like:

```
store_seed_data/
├── manifest.json              # Index of all packages
├── skills/
│   ├── factory/               # Built-in Pantheon skills
│   │   ├── omics_quality_control.md
│   │   └── ...
│   ├── labclaw/               # From github.com/wu-yc/LabClaw
│   ├── openclaw-medical/      # From github.com/FreedomIntelligence/OpenClaw-Medical-Skills
│   ├── claude-scientific/     # From github.com/K-Dense-AI/claude-scientific-skills
│   └── clawbio/               # From github.com/ClawBio/ClawBio
├── agents/                    # Built-in factory agents
│   ├── researcher.md
│   └── ...
└── teams/                     # Built-in factory teams
    ├── default.md
    └── ...
```

### manifest.json format

Each entry:
```json
{
  "name": "omics_cell_type_annotation",
  "type": "skill",
  "display_name": "Cell Type Annotation",
  "description": "Approaches for annotating cell types...",
  "category": "single-cell",
  "tags": ["annotation", "cell types", "markers"],
  "source": "Pantheon",
  "source_url": null,
  "file": "skills/factory/omics_cell_type_annotation.md",
  "bundled_files": {}
}
```

Key fields:
- `name` - unique identifier
- `type` - `"skill"`, `"agent"`, or `"team"`
- `source` - attribution label (`"Pantheon"`, `"LabClaw"`, etc.)
- `file` - relative path to the .md file
- `bundled_files` - additional files (e.g., agents bundled with a team)

---

## External Repositories

The seed system automatically clones and imports from these repos:

| Source | Repository | Skills Dir |
|--------|-----------|-----------|
| LabClaw | `github.com/wu-yc/LabClaw` | `skills/` |
| OpenClaw Medical Skills | `github.com/FreedomIntelligence/OpenClaw-Medical-Skills` | `skills/` |
| Claude Scientific Skills | `github.com/K-Dense-AI/claude-scientific-skills` | `scientific-skills/` |
| ClawBio | `github.com/ClawBio/ClawBio` | `skills/` |

Each external skill gets an attribution header:
```markdown
> **Source**: [LabClaw](https://github.com/wu-yc/LabClaw) | License: MIT
```

To add a new external repo, edit `EXTERNAL_REPOS` in `pantheonos/store/seed.py`.

---

## Where Packages Are Installed

When a user installs a package (via CLI or UI), files go to:

| Type | Install Path |
|------|-------------|
| Skills | `~/.pantheon/skills/{name}.md` |
| Agents | `~/.pantheon/agents/{name}.md` |
| Teams | `~/.pantheon/teams/{name}.md` |

---

## Hub API Endpoints (for reference)

| Method | Endpoint | Auth | Description |
|--------|---------|------|-------------|
| GET | `/api/store/packages` | No | Search/list packages |
| GET | `/api/store/packages/stats` | No | Get totals by type/category/source |
| GET | `/api/store/packages/{id}` | No | Package details |
| GET | `/api/store/packages/{id}/download` | No | Download latest version |
| POST | `/api/store/packages` | Yes | Publish new package |
| POST | `/api/store/packages/{id}/versions` | Yes | Publish new version |
| PUT | `/api/store/packages/{id}` | Yes | Update package metadata |
| DELETE | `/api/store/packages/{id}` | Yes | Delete package |

---

## Troubleshooting

- **409 Conflict on publish**: Package name already exists. The seed publisher skips these automatically.
- **Auth errors**: Run `pantheon store login` again. Token may have expired.
- **External repo clone fails**: Check network/git access. Repos are cloned with `--depth 1`.
- **Missing packages after seed**: Check the terminal output for "failed" count. Re-run `pantheon store seed publish` (already-published packages are skipped).

---

## Key Source Files

| File | Description |
|------|-------------|
| `pantheonos/store/cli.py` | CLI commands (`pantheon store ...`) |
| `pantheonos/store/seed.py` | Seed prepare/publish logic |
| `pantheonos/store/client.py` | HTTP client for Hub API |
| `pantheonos/store/publisher.py` | Package collector for publishing |
| `pantheonos/store/installer.py` | Local install/uninstall |
| `pantheonos/store/auth.py` | Auth credential management |
| `apps/agent/pantheon/factory/templates/` | Factory skill/agent/team templates |
