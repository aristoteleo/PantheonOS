"""
Setup Wizard - TUI wizard for configuring LLM provider API keys.

Launched automatically when no API keys are detected at REPL startup.
Guides the user through selecting providers and entering API keys,
then saves them to ~/.pantheon/.env for persistence across sessions.

Also provides PROVIDER_MENU and _save_key_to_env_file used by the
/keys REPL command.
"""

import os
import re
import json
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from pantheon.utils.model_selector import PROVIDER_API_KEYS, CUSTOM_ENDPOINT_ENVS, CustomEndpointConfig
from pantheon.utils.log import logger


# ============ Data Classes for Better Readability ============

@dataclass
class ProviderMenuEntry:
    """Provider menu entry with named fields for better readability."""
    provider_key: str
    display_name: str
    env_var: str
    is_custom: bool = False
    custom_config: Optional[CustomEndpointConfig] = None


# Providers shown in the wizard/keys menu
PROVIDER_MENU = [
    ProviderMenuEntry("openai", "OpenAI", "OPENAI_API_KEY"),
    ProviderMenuEntry("openai_oauth", "OpenAI (OAuth)", None),  # OAuth doesn't require API key
    ProviderMenuEntry("anthropic", "Anthropic", "ANTHROPIC_API_KEY"),
    ProviderMenuEntry("gemini", "Google Gemini", "GEMINI_API_KEY"),
    ProviderMenuEntry("google", "Google AI", "GOOGLE_API_KEY"),
    ProviderMenuEntry("azure", "Azure OpenAI", "AZURE_API_KEY"),
    ProviderMenuEntry("zai", "Z.ai (Zhipu)", "ZAI_API_KEY"),
    ProviderMenuEntry("minimax", "MiniMax", "MINIMAX_API_KEY"),
    ProviderMenuEntry("moonshot", "Moonshot", "MOONSHOT_API_KEY"),
    ProviderMenuEntry("deepseek", "DeepSeek", "DEEPSEEK_API_KEY"),
    ProviderMenuEntry("mistral", "Mistral", "MISTRAL_API_KEY"),
    ProviderMenuEntry("groq", "Groq", "GROQ_API_KEY"),
    ProviderMenuEntry("openrouter", "OpenRouter", "OPENROUTER_API_KEY"),
    ProviderMenuEntry("together_ai", "Together AI", "TOGETHER_API_KEY"),
    ProviderMenuEntry("cohere", "Cohere", "COHERE_API_KEY"),
    ProviderMenuEntry("replicate", "Replicate", "REPLICATE_API_KEY"),
    ProviderMenuEntry("huggingface", "Hugging Face", "HUGGINGFACE_API_KEY"),
]

# Build custom endpoint menu entries from CUSTOM_ENDPOINT_ENVS
CUSTOM_ENDPOINT_MENU = [
    ProviderMenuEntry(
        provider_key=config.provider_key,
        display_name=config.display_name,
        env_var=config.api_key_env,
        is_custom=True,
        custom_config=config,
    )
    for config in CUSTOM_ENDPOINT_ENVS.values()
]

def check_and_run_setup():
    """Check if any LLM provider API keys or OAuth tokens are set; launch wizard if none found.

    Called at startup before the event loop starts (sync context).
    Also checks for universal LLM_API_KEY (custom API endpoint) and
    custom endpoint keys (CUSTOM_*_API_KEY).
    Also checks for OpenAI OAuth tokens.

    Skips the wizard if:
    - Any API key is already configured
    - OpenAI OAuth token is already saved
    - SKIP_SETUP_WIZARD environment variable is set
    """
    # Check if user explicitly wants to skip setup
    if os.environ.get("SKIP_SETUP_WIZARD", "").lower() in ("1", "true", "yes"):
        return

    # Check standard provider keys
    for env_var in PROVIDER_API_KEYS.values():
        if os.environ.get(env_var, ""):
            return

    # Check custom endpoint keys
    for config in CUSTOM_ENDPOINT_ENVS.values():
        if os.environ.get(config.api_key_env, ""):
            return

    # Check for OpenAI OAuth token
    try:
        from pantheon.auth.oauth_manager import is_oauth_available
        if is_oauth_available("openai"):
            return
    except Exception:
        pass

    # Check legacy universal LLM_API_KEY (with deprecation warning)
    if os.environ.get("LLM_API_KEY", ""):
        if os.environ.get("LLM_API_BASE", ""):
            logger.warning(
                "LLM_API_BASE is deprecated. Please use CUSTOM_OPENAI_API_BASE or "
                "CUSTOM_ANTHROPIC_API_BASE instead for better control over custom endpoints. "
                "See documentation for details."
            )
        return

    # No API keys or OAuth found - launch wizard
    run_setup_wizard()


def run_setup_wizard(standalone: bool = False):
    """Interactive TUI wizard (sync, for use before event loop starts).

    Args:
        standalone: If True, called via ``pantheon setup`` (skip "Starting Pantheon" message).
    """
    from rich.console import Console
    from rich.panel import Panel
    from prompt_toolkit import prompt as pt_prompt

    console = Console()

    # Header
    console.print()
    console.print(
        Panel(
            "  No LLM provider API keys detected.\n"
            "  Let's set up at least one provider to get started.\n"
            "  You can also use [bold]/keys[/bold] command in the REPL to configure later.",
            title="Pantheon Setup",
            border_style="cyan",
        )
    )

    configured_any = False

    while True:
        # Show legacy custom API endpoint option (with deprecation notice)
        legacy_set = " [green](configured)[/green]" if os.environ.get("LLM_API_KEY", "") else ""
        console.print(f"\n  [cyan][0][/cyan] Custom API Endpoint  (LLM_API_BASE + LLM_API_KEY) [dim](deprecated)[/dim]{legacy_set}")

        # Show custom endpoint options
        console.print("\nCustom Endpoints:")
        for i, entry in enumerate(CUSTOM_ENDPOINT_MENU, 1):
            already_set = " [green](configured)[/green]" if os.environ.get(entry.env_var, "") else ""
            console.print(f"  [cyan]\\[c{i}][/] {entry.display_name:<20} ({entry.env_var}){already_set}")

        # Show provider menu
        console.print("\nStandard Providers:")
        for i, entry in enumerate(PROVIDER_MENU, 1):
            # Handle OAuth providers which don't have env_var
            if entry.env_var is None:
                already_set = ""
            else:
                already_set = " [green](configured)[/green]" if os.environ.get(entry.env_var, "") else ""
            console.print(f"  [cyan][{i}][/cyan] {entry.display_name:<20} ({entry.env_var or 'OAuth'}){already_set}")
        console.print()
        console.print("[dim]  Prefix with 'd' to delete, e.g. d0, d1,d3[/dim]")
        console.print()

        try:
            selection = pt_prompt("Select providers to configure (comma-separated, e.g. 0,1,3,c1): ")
        except (EOFError, KeyboardInterrupt):
            console.print("\nSetup cancelled.")
            break

        # Parse selection
        standard_indices = []
        custom_indices = []
        has_legacy_custom = False
        delete_standard_indices = []
        delete_custom_indices = []
        delete_legacy_custom = False

        for part in selection.split(","):
            part = part.strip().lower()
            if part.startswith("d"):
                # Delete mode
                num = part[1:]
                if num == "0":
                    delete_legacy_custom = True
                elif num.startswith("c") and num[1:].isdigit():
                    idx = int(num[1:])
                    if 1 <= idx <= len(CUSTOM_ENDPOINT_MENU):
                        delete_custom_indices.append(idx - 1)
                elif num.isdigit():
                    idx = int(num)
                    if 1 <= idx <= len(PROVIDER_MENU):
                        delete_standard_indices.append(idx - 1)
            elif part == "0":
                has_legacy_custom = True
            elif part.startswith("c") and part[1:].isdigit():
                idx = int(part[1:])
                if 1 <= idx <= len(CUSTOM_ENDPOINT_MENU):
                    custom_indices.append(idx - 1)
            elif part.isdigit():
                idx = int(part)
                if 1 <= idx <= len(PROVIDER_MENU):
                    standard_indices.append(idx - 1)

        # Handle deletions
        if delete_legacy_custom:
            _remove_custom_endpoint_from_env("legacy")
            console.print("[green]\u2713 Legacy Custom API Endpoint removed[/green]")

        for idx in delete_custom_indices:
            entry = CUSTOM_ENDPOINT_MENU[idx]
            _remove_custom_endpoint_from_env(entry.provider_key)
            console.print(f"[green]\u2713 {entry.display_name} removed[/green]")

        for idx in delete_standard_indices:
            entry = PROVIDER_MENU[idx]

            # Special handling for OAuth providers
            if entry.provider_key == "openai_oauth":
                try:
                    from pantheon.auth.oauth_manager import get_oauth_manager
                    oauth_manager = get_oauth_manager()
                    oauth_manager.logout("openai")
                    console.print(f"[green]✓ {entry.display_name} credentials cleared[/green]")
                except Exception as e:
                    logger.warning(f"Failed to clear OAuth credentials: {e}")
                    console.print(f"[yellow]Failed to clear {entry.display_name}: {e}[/yellow]")
            else:
                _remove_key_from_env_file(entry.env_var)
                console.print(f"[green]\u2713 {entry.display_name} ({entry.env_var}) removed[/green]")

        if (delete_legacy_custom or delete_custom_indices or delete_standard_indices) and not standard_indices and not custom_indices and not has_legacy_custom:
            console.print()
            try:
                more = pt_prompt("Continue? [y/N]: ")
            except (EOFError, KeyboardInterrupt):
                more = "n"
            if more.strip().lower() != "y":
                break
            continue

        if not standard_indices and not custom_indices and not has_legacy_custom:
            console.print("[yellow]No valid providers selected. Please try again.[/yellow]")
            continue

        # Handle legacy custom API endpoint
        if has_legacy_custom:
            console.print("\n[bold yellow]Warning: LLM_API_BASE is deprecated[/bold yellow]")
            console.print("[dim]Consider using CUSTOM_OPENAI_API_BASE or CUSTOM_ANTHROPIC_API_BASE instead.[/dim]")
            console.print("\n[bold]Configure Custom API Endpoint[/bold]")
            try:
                base_url = pt_prompt("LLM_API_BASE (e.g. https://your-proxy.com/v1): ")
            except (EOFError, KeyboardInterrupt):
                console.print("\nSkipped.")
                base_url = ""

            base_url = base_url.strip()
            if base_url:
                _save_key_to_env_file("LLM_API_BASE", base_url)
                os.environ["LLM_API_BASE"] = base_url
                console.print("[green]\u2713 LLM_API_BASE saved[/green]")

            try:
                api_key = pt_prompt("LLM_API_KEY: ", is_password=True)
            except (EOFError, KeyboardInterrupt):
                console.print("\nSkipped.")
                api_key = ""

            api_key = api_key.strip()
            if api_key:
                _save_key_to_env_file("LLM_API_KEY", api_key)
                os.environ["LLM_API_KEY"] = api_key
                console.print("[green]\u2713 LLM_API_KEY saved[/green]")
                configured_any = True
            elif not base_url:
                console.print("[yellow]Nothing entered, skipped.[/yellow]")

        # Handle custom endpoint configurations
        for idx in custom_indices:
            entry = CUSTOM_ENDPOINT_MENU[idx]
            config = entry.custom_config
            console.print(f"\n[bold]Configure {entry.display_name}[/bold]")

            # API Base URL
            try:
                base_url = pt_prompt(f"{config.api_base_env} (e.g. https://your-anthropic-proxy.com/v1): ")
            except (EOFError, KeyboardInterrupt):
                console.print("\nSkipped.")
                base_url = ""

            base_url = base_url.strip()
            if base_url:
                _save_key_to_env_file(config.api_base_env, base_url)
                os.environ[config.api_base_env] = base_url
                console.print(f"[green]\u2713 {config.api_base_env} saved[/green]")

            # API Key
            try:
                api_key = pt_prompt(f"{config.api_key_env}: ", is_password=True)
            except (EOFError, KeyboardInterrupt):
                console.print("\nSkipped.")
                api_key = ""

            api_key = api_key.strip()
            if api_key:
                _save_key_to_env_file(config.api_key_env, api_key)
                os.environ[config.api_key_env] = api_key
                console.print(f"[green]\u2713 {config.api_key_env} saved[/green]")

            # Model name
            try:
                model_name = pt_prompt(f"{config.model_env} (e.g. claude-3-5-sonnet-20241022): ")
            except (EOFError, KeyboardInterrupt):
                console.print("\nSkipped.")
                model_name = ""

            model_name = model_name.strip()
            if model_name:
                _save_key_to_env_file(config.model_env, model_name)
                os.environ[config.model_env] = model_name
                _save_custom_model_to_settings(entry.provider_key, model_name)
                console.print(f"[green]\u2713 {config.model_env} saved[/green]")
                configured_any = True
            elif not base_url and not api_key:
                console.print("[yellow]Nothing entered, skipped.[/yellow]")

        # Collect API keys for selected standard providers
        for idx in standard_indices:
            entry = PROVIDER_MENU[idx]

            # Special handling for OAuth providers (no API key needed)
            if entry.provider_key == "openai_oauth":
                console.print(f"\n[bold]Configure {entry.display_name}[/bold]")
                console.print("[dim]OAuth login will be handled through the CLI.[/dim]")
                console.print("[dim]Use '/oauth login' command in Pantheon REPL to authenticate.[/dim]")
                console.print("[green]✓ OpenAI OAuth provider configured[/green]")
                configured_any = True
                continue

            console.print(f"\n[bold]Enter API key for {entry.display_name}[/bold]")
            try:
                api_key = pt_prompt(f"{entry.env_var}: ", is_password=True)
            except (EOFError, KeyboardInterrupt):
                console.print("\nSkipped.")
                continue

            api_key = api_key.strip()
            if not api_key:
                console.print("[yellow]Empty key, skipped.[/yellow]")
                continue

            _save_key_to_env_file(entry.env_var, api_key)
            os.environ[entry.env_var] = api_key
            console.print("[green]\u2713 Saved[/green]")
            configured_any = True

        console.print()
        try:
            more = pt_prompt("Configure another provider? [y/N]: ")
        except (EOFError, KeyboardInterrupt):
            more = "n"
        if more.strip().lower() != "y":
            break

    if configured_any:
        env_path = Path.home() / ".pantheon" / ".env"
        console.print(f"\n[green]\u2713 API keys saved to {env_path}[/green]")
        if not standalone:
            console.print("  Starting Pantheon...\n")
    else:
        console.print(
            "\n[yellow]No API keys configured. "
            "Pantheon may not work correctly without provider keys.[/yellow]\n"
        )

    return configured_any


def _save_key_to_env_file(env_var: str, value: str):
    """Append or update a key in ~/.pantheon/.env."""
    env_dir = Path.home() / ".pantheon"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_file = env_dir / ".env"

    # Read existing content to check for duplicates
    lines = []
    if env_file.exists():
        lines = env_file.read_text().splitlines()

    # Remove existing entry for this var if present
    lines = [line for line in lines if not line.startswith(f"{env_var}=")]

    # Append new entry
    lines.append(f"{env_var}={value}")

    env_file.write_text("\n".join(lines) + "\n")


def _remove_key_from_env_file(env_var: str):
    """Remove a key from ~/.pantheon/.env and unset from environment."""
    env_file = Path.home() / ".pantheon" / ".env"
    if env_file.exists():
        lines = env_file.read_text().splitlines()
        lines = [line for line in lines if not line.startswith(f"{env_var}=")]
        env_file.write_text("\n".join(lines) + "\n" if lines else "")
    os.environ.pop(env_var, None)


def _remove_custom_endpoint_from_env(provider_key: str):
    """Remove custom endpoint configuration from ~/.pantheon/.env and settings.json.

    Args:
        provider_key: Either "legacy" for LLM_API_BASE/LLM_API_KEY,
                      or a custom provider key like "custom_anthropic"
    """
    if provider_key == "legacy":
        _remove_key_from_env_file("LLM_API_BASE")
        _remove_key_from_env_file("LLM_API_KEY")
    elif provider_key in CUSTOM_ENDPOINT_ENVS:
        config = CUSTOM_ENDPOINT_ENVS[provider_key]
        _remove_key_from_env_file(config.api_key_env)
        _remove_key_from_env_file(config.api_base_env)
        _remove_key_from_env_file(config.model_env)
        # Also remove from settings.json
        _remove_custom_model_from_settings(provider_key)


def _save_custom_model_to_settings(provider_key: str, model_name: str):
    """Save custom model to settings.json while preserving comments.

    Uses regex to update only the custom_models section without
    destroying user comments in the file.

    Args:
        provider_key: Custom provider key (e.g., "custom_anthropic")
        model_name: Model name to save
    """
    settings_path = Path.home() / ".pantheon" / "settings.json"
    if not settings_path.exists():
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        template = Path(__file__).parent.parent / "factory" / "templates" / "settings.json"
        if template.exists():
            shutil.copy(template, settings_path)
            logger.debug(f"Created {settings_path} from factory template")
        else:
            return

    try:
        content = settings_path.read_text()

        # Check if custom_models section exists
        if '"custom_models"' not in content:
            # Add custom_models section before the closing brace of the main object
            # Find the last closing brace that's not inside a string
            insert_pos = content.rfind("}")
            if insert_pos > 0:
                new_section = f',\n    "custom_models": {{\n        "{provider_key}": "{model_name}"\n    }}\n'
                new_content = content[:insert_pos] + new_section + content[insert_pos:]
                settings_path.write_text(new_content)
                logger.debug(f"Added custom_models section with {provider_key} to settings.json")
            return

        # Find and update existing entry in custom_models
        # Pattern to match the provider_key entry within custom_models
        provider_pattern = rf'("{provider_key}"\s*:\s*)"[^"]*"'

        if re.search(provider_pattern, content):
            # Update existing entry
            new_content = re.sub(
                provider_pattern,
                rf'\1"{model_name}"',
                content
            )
            settings_path.write_text(new_content)
            logger.debug(f"Updated {provider_key} in settings.json")
        else:
            # Add new entry to custom_models
            # Find the custom_models section and add before its closing brace
            custom_models_pattern = r'("custom_models"\s*:\s*\{)'
            match = re.search(custom_models_pattern, content)
            if match:
                insert_pos = match.end()
                new_entry = f'\n        "{provider_key}": "{model_name}",'
                new_content = content[:insert_pos] + new_entry + content[insert_pos:]
                # Clean up trailing comma before closing brace
                new_content = re.sub(r',(\s*\n\s*)\}', r'\1}', new_content)
                settings_path.write_text(new_content)
                logger.debug(f"Added {provider_key} to custom_models in settings.json")
    except Exception as e:
        logger.warning(f"Failed to save custom model to settings.json: {e}")


def _remove_custom_model_from_settings(provider_key: str):
    """Remove custom model from settings.json while preserving comments.

    Args:
        provider_key: Custom provider key to remove
    """
    settings_path = Path.home() / ".pantheon" / "settings.json"
    if not settings_path.exists():
        return

    try:
        content = settings_path.read_text()

        # Remove the provider_key entry from custom_models
        # Pattern matches: "provider_key": "any_value",
        # or "provider_key": "any_value" (at end of object)
        patterns = [
            rf'\s*"{provider_key}"\s*:\s*"[^"]*",?',  # Entry with optional trailing comma
        ]

        for pattern in patterns:
            new_content = re.sub(pattern, "", content)
            if new_content != content:
                # Clean up any trailing commas before closing braces
                new_content = re.sub(r',(\s*\n\s*)\}', r'\1}', new_content)
                # Clean up empty custom_models section
                new_content = re.sub(
                    r'"custom_models"\s*:\s*\{\s*\}',
                    r'"custom_models": {}',
                    new_content
                )
                settings_path.write_text(new_content)
                logger.debug(f"Removed {provider_key} from settings.json")
                break
    except Exception as e:
        logger.warning(f"Failed to remove custom model from settings.json: {e}")
