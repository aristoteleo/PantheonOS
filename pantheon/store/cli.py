"""Pantheon Store CLI commands.

Usage:
    pantheon store login [--hub-url URL]
    pantheon store logout
    pantheon store search [QUERY] [--type TYPE] [--category CAT]
    pantheon store publish ITEM_ID --type TYPE [--hub-url URL]
    pantheon store install PACKAGE [--version VER]
    pantheon store update PACKAGE
    pantheon store uninstall PACKAGE
    pantheon store info PACKAGE
    pantheon store list [--what installed|published]
    pantheon store seed [--source SOURCE] [--dry-run]
"""

import asyncio
import uuid

from rich.console import Console
from rich.table import Table

from .auth import StoreAuth
from .client import StoreClient


console = Console()


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


class StoreCLI:
    """Pantheon Store - publish, install, and manage agent/team/skill packages."""

    def login(self, hub_url: str = None, username: str = None, password: str = None):
        """Login to Pantheon Store.

        Args:
            hub_url: Hub server URL (default: https://pantheon.aristoteleo.com)
            username: Username (prompted if not provided)
            password: Password (prompted if not provided)
        """
        from prompt_toolkit import prompt as pt_prompt

        if not username:
            username = pt_prompt("Username: ")
        if not password:
            password = pt_prompt("Password: ", is_password=True)

        client = StoreClient(hub_url=hub_url)
        data = _run(client.login(username, password))
        console.print(f"[green]Logged in as {username}[/green]")
        return data

    def logout(self):
        """Logout from Pantheon Store."""
        auth = StoreAuth()
        if auth.is_logged_in:
            auth.clear()
            console.print("[green]Logged out.[/green]")
        else:
            console.print("[yellow]Not logged in.[/yellow]")

    def search(self, query: str = None, type: str = None, category: str = None,
               limit: int = 20):
        """Search packages in the Store.

        Args:
            query: Search query string
            type: Filter by type (agent, team, skill)
            category: Filter by category
            limit: Max results (default: 20)
        """
        client = StoreClient()
        data = _run(client.search(q=query, type=type, category=category, limit=limit))

        packages = data.get("packages", [])
        if not packages:
            console.print("[yellow]No packages found.[/yellow]")
            return

        table = Table(title=f"Store Packages ({len(packages)} results)")
        table.add_column("Name", style="cyan")
        table.add_column("Type", style="magenta")
        table.add_column("Description")
        table.add_column("Author", style="green")
        table.add_column("Downloads", justify="right")
        table.add_column("Version")

        for pkg in packages:
            table.add_row(
                pkg.get("name", ""),
                pkg.get("type", ""),
                (pkg.get("description") or "")[:60],
                pkg.get("author_username", ""),
                str(pkg.get("downloads", 0)),
                pkg.get("latest_version", ""),
            )

        console.print(table)

    def publish(self, item_id: str, type: str, version: str = "1.0.0",
                display_name: str = None, description: str = None,
                category: str = "general", hub_url: str = None):
        """Publish an agent, team, or skill to the Store.

        Args:
            item_id: ID of the agent/team/skill to publish
            type: Package type (agent, team, skill)
            version: Version string (default: 1.0.0)
            display_name: Display name (defaults to item_id)
            description: Package description
            category: Category (default: general)
            hub_url: Hub server URL
        """
        from .publisher import PackageCollector

        if type not in ("agent", "team", "skill"):
            raise SystemExit("--type must be one of: agent, team, skill")

        collector = PackageCollector()
        try:
            content, files = collector.collect(item_id, type)
        except FileNotFoundError as e:
            raise SystemExit(str(e))

        client = StoreClient(hub_url=hub_url)
        name = item_id.replace("/", "_")  # Normalize namespaced IDs

        # Check if package already exists — publish new version if so
        try:
            existing = _run(client.search(q=name, type=type, limit=1))
            pkgs = existing.get("packages", [])
            match = next((p for p in pkgs if p.get("name") == name), None)

            if match:
                # Package exists — publish new version
                pkg_id = match["id"]
                data = _run(client.publish_version(pkg_id, {
                    "version": version,
                    "content": content,
                    "files": files,
                }))
                console.print(
                    f"[green]Published new version {version} of "
                    f"{type} '{name}'[/green]"
                )
                return data
        except Exception:
            pass  # Fall through to create new package

        # Create new package
        data = _run(client.publish({
            "name": name,
            "type": type,
            "display_name": display_name or item_id,
            "description": description or "",
            "category": category,
            "version": version,
            "content": content,
            "files": files,
        }))

        console.print(f"[green]Published {type} '{name}' v{version}[/green]")
        return data

    def install(self, package: str, version: str = None, hub_url: str = None):
        """Install a package from the Store.

        Args:
            package: Package name or ID
            version: Specific version (default: latest)
            hub_url: Hub server URL
        """
        from .installer import PackageInstaller

        client = StoreClient(hub_url=hub_url)

        # Resolve package — try by name first
        try:
            pkg_info = _run(client.search(q=package, limit=1))
            pkgs = pkg_info.get("packages", [])
            match = next((p for p in pkgs if p.get("name") == package), None)
            if match:
                pkg_id = match["id"]
            else:
                pkg_id = package  # Assume it's a direct ID
        except Exception:
            pkg_id = package

        # Download
        download = _run(client.download(pkg_id, version))

        pkg_name = download.get("name", package)
        pkg_type = download.get("type", "agent")
        pkg_version = download.get("version", "unknown")
        content = download.get("content", "")
        files = download.get("files") or {}

        # Install locally
        installer = PackageInstaller()
        written = installer.install(pkg_type, pkg_name, content, files)

        # Record install if logged in
        auth = StoreAuth()
        if auth.is_logged_in:
            try:
                _run(client.record_install(pkg_id, pkg_version))
            except Exception:
                pass  # Non-critical

        console.print(f"[green]Installed {pkg_type} '{pkg_name}' v{pkg_version}[/green]")
        for p in written:
            console.print(f"  → {p}")

    def uninstall(self, package: str, type: str = None, hub_url: str = None):
        """Uninstall a package.

        Args:
            package: Package name
            type: Package type (agent, team, skill). If not provided, tries all.
            hub_url: Hub server URL
        """
        from .installer import PackageInstaller

        installer = PackageInstaller()

        if type:
            removed = installer.uninstall(type, package)
        else:
            # Try all types
            removed = []
            for t in ("agent", "team", "skill"):
                removed.extend(installer.uninstall(t, package))

        if removed:
            console.print(f"[green]Uninstalled '{package}'[/green]")
            for p in removed:
                console.print(f"  ✗ {p}")

            # Record uninstall if logged in
            auth = StoreAuth()
            if auth.is_logged_in:
                client = StoreClient(hub_url=hub_url)
                try:
                    _run(client.record_uninstall(package))
                except Exception:
                    pass
        else:
            console.print(f"[yellow]Package '{package}' not found locally.[/yellow]")

    def update(self, package: str, hub_url: str = None):
        """Update an installed package to the latest version.

        Args:
            package: Package name
            hub_url: Hub server URL
        """
        # Re-install with latest version
        self.install(package, version=None, hub_url=hub_url)
        console.print("[green]Updated to latest version.[/green]")

    def info(self, package: str, hub_url: str = None):
        """Show detailed information about a package.

        Args:
            package: Package name or ID
            hub_url: Hub server URL
        """
        client = StoreClient(hub_url=hub_url)

        # Try to find by name
        try:
            result = _run(client.search(q=package, limit=1))
            pkgs = result.get("packages", [])
            match = next((p for p in pkgs if p.get("name") == package), None)
            if match:
                pkg_id = match["id"]
            else:
                pkg_id = package
        except Exception:
            pkg_id = package

        pkg = _run(client.get_package(pkg_id))

        console.print()
        console.print(f"[bold cyan]{pkg.get('icon', '📦')} {pkg.get('display_name', pkg.get('name', ''))}[/bold cyan]")
        console.print(f"  Name:        {pkg.get('name', '')}")
        console.print(f"  Type:        {pkg.get('type', '')}")
        console.print(f"  Author:      {pkg.get('author_username', '')}")
        console.print(f"  Category:    {pkg.get('category', '')}")
        console.print(f"  Downloads:   {pkg.get('downloads', 0)}")
        console.print(f"  Version:     {pkg.get('latest_version', 'N/A')}")
        console.print(f"  Public:      {pkg.get('is_public', True)}")

        desc = pkg.get("description") or ""
        if desc:
            console.print(f"  Description: {desc}")

        tags = pkg.get("tags") or []
        if tags:
            console.print(f"  Tags:        {', '.join(tags)}")

        # Show versions
        try:
            versions = _run(client.list_versions(pkg_id))
            ver_list = versions.get("versions", [])
            if ver_list:
                console.print(f"\n[bold]Versions ({len(ver_list)}):[/bold]")
                for v in ver_list[:10]:
                    console.print(f"  {v.get('version', '')}  ({v.get('created_at', '')[:10]})")
        except Exception:
            pass

        console.print()

    def seed(self, action: str = "prepare", output_dir: str = "store_seed_data",
             hub_url: str = None, dry_run: bool = False):
        """Seed the store with initial skills, agents, and teams.

        Two-step workflow:
          1. prepare: Collect all packages into a local directory
          2. publish: Batch-publish from the prepared directory to Hub

        Args:
            action: "prepare" or "publish"
            output_dir: Directory for prepared data (default: store_seed_data)
            hub_url: Hub server URL (for publish)
            dry_run: Preview without publishing (for publish)
        """
        from .seed import StoreSeed

        seeder = StoreSeed(hub_url=hub_url)

        if action == "prepare":
            seeder.prepare(output_dir=output_dir)
        elif action == "publish":
            seeder.publish_prepared(input_dir=output_dir, dry_run=dry_run)
        else:
            raise SystemExit(
                f"Unknown action: {action}. Options: prepare, publish"
            )

    def list(self, what: str = "installed", hub_url: str = None):
        """List installed or published packages.

        Args:
            what: "installed" or "published" (default: installed)
            hub_url: Hub server URL
        """
        client = StoreClient(hub_url=hub_url)

        if what == "published":
            data = _run(client.my_published())
            packages = data.get("packages", [])
            title = "My Published Packages"
        elif what == "installed":
            data = _run(client.my_installed())
            packages = data.get("installs", [])
            title = "My Installed Packages"
        else:
            raise SystemExit("--what must be 'installed' or 'published'")

        if not packages:
            console.print(f"[yellow]No {what} packages.[/yellow]")
            return

        table = Table(title=title)

        if what == "published":
            table.add_column("Name", style="cyan")
            table.add_column("Type", style="magenta")
            table.add_column("Downloads", justify="right")
            table.add_column("Version")

            for pkg in packages:
                table.add_row(
                    pkg.get("name", ""),
                    pkg.get("type", ""),
                    str(pkg.get("downloads", 0)),
                    pkg.get("latest_version", ""),
                )
        else:
            table.add_column("Package", style="cyan")
            table.add_column("Type", style="magenta")
            table.add_column("Version")
            table.add_column("Installed", style="dim")

            for inst in packages:
                table.add_row(
                    inst.get("package_name", ""),
                    inst.get("package_type", ""),
                    inst.get("version", ""),
                    (inst.get("installed_at") or "")[:10],
                )

        console.print(table)
