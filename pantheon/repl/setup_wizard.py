"""
Setup Wizard - TUI wizard for configuring LLM provider API keys.

Launched automatically when no API keys are detected at REPL startup.
Guides the user through selecting providers and entering API keys,
then saves them to ~/.pantheon/.env for persistence across sessions.

Also provides PROVIDER_MENU and env helpers used by the /keys REPL command.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from pantheon.settings import get_settings


@dataclass
class ProviderMenuEntry:
    """Provider menu entry used by the setup wizard and /keys command."""

    provider_key: str
    display_name: str
    env_var: str
    base_env_var: str | None = None


PROVIDER_MENU = [
    ProviderMenuEntry("openai", "OpenAI", "OPENAI_API_KEY", "OPENAI_API_BASE"),
    ProviderMenuEntry("anthropic", "Anthropic", "ANTHROPIC_API_KEY", "ANTHROPIC_API_BASE"),
    ProviderMenuEntry("gemini", "Google Gemini", "GEMINI_API_KEY", "GEMINI_API_BASE"),
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


def _has_any_provider_configured() -> bool:
    settings = get_settings()

    for entry in PROVIDER_MENU:
        if settings.get_api_key(entry.env_var):
            return True

    return bool(
        settings.get_api_key("LLM_API_BASE") and settings.get_api_key("LLM_API_KEY")
    )


def check_and_run_setup():
    """Launch the setup wizard only when no usable LLM credentials are configured."""
    if os.environ.get("SKIP_SETUP_WIZARD", "").lower() in ("1", "true", "yes"):
        return

    if _has_any_provider_configured():
        return

    run_setup_wizard()


def run_setup_wizard(standalone: bool = False):
    """Interactive TUI wizard (sync, for use before event loop starts)."""
    from prompt_toolkit import prompt as pt_prompt
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    configured_any = False

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

    while True:
        fallback_set = " [green](configured)[/green]" if _fallback_is_configured() else ""
        console.print(
            f"\n  [cyan][0][/cyan] OpenAI-Compatible Fallback"
            f"  (LLM_API_BASE + LLM_API_KEY){fallback_set}"
        )

        console.print("\nProviders:")
        for i, entry in enumerate(PROVIDER_MENU, 1):
            already_set = " [green](configured)[/green]" if _provider_is_configured(entry) else ""
            base_hint = " + Base URL" if entry.base_env_var else ""
            console.print(
                f"  [cyan][{i}][/cyan] {entry.display_name:<20} ({entry.env_var}{base_hint}){already_set}"
            )
        console.print()
        console.print("[dim]  Prefix with 'd' to delete, e.g. d0, d1,d3[/dim]")
        console.print()

        try:
            selection = pt_prompt("Select providers to configure (comma-separated, e.g. 0,1,3): ")
        except (EOFError, KeyboardInterrupt):
            console.print("\nSetup cancelled.")
            break

        selected_indices: list[int] = []
        delete_indices: list[int] = []
        configure_fallback = False
        delete_fallback = False

        for part in selection.split(","):
            token = part.strip().lower()
            if not token:
                continue
            if token.startswith("d"):
                target = token[1:]
                if target == "0":
                    delete_fallback = True
                elif target.isdigit():
                    idx = int(target)
                    if 1 <= idx <= len(PROVIDER_MENU):
                        delete_indices.append(idx - 1)
                continue
            if token == "0":
                configure_fallback = True
                continue
            if token.isdigit():
                idx = int(token)
                if 1 <= idx <= len(PROVIDER_MENU):
                    selected_indices.append(idx - 1)

        if delete_fallback:
            _remove_key_from_env_file("LLM_API_BASE")
            _remove_key_from_env_file("LLM_API_KEY")
            console.print("[green]✓ OpenAI-compatible fallback removed[/green]")

        for idx in delete_indices:
            entry = PROVIDER_MENU[idx]
            _remove_key_from_env_file(entry.env_var)
            if entry.base_env_var:
                _remove_key_from_env_file(entry.base_env_var)
            console.print(f"[green]✓ {entry.display_name} removed[/green]")

        if (
            delete_fallback or delete_indices
        ) and not selected_indices and not configure_fallback:
            try:
                more = pt_prompt("Continue? [y/N]: ")
            except (EOFError, KeyboardInterrupt):
                more = "n"
            if more.strip().lower() != "y":
                break
            continue

        if not selected_indices and not configure_fallback:
            console.print("[yellow]No valid providers selected. Please try again.[/yellow]")
            continue

        if configure_fallback:
            configured_any = _configure_fallback(console, pt_prompt) or configured_any

        for idx in selected_indices:
            configured_any = _configure_provider(PROVIDER_MENU[idx], console, pt_prompt) or configured_any

        console.print()
        try:
            more = pt_prompt("Configure another provider? [y/N]: ")
        except (EOFError, KeyboardInterrupt):
            more = "n"
        if more.strip().lower() != "y":
            break

    if configured_any:
        env_path = Path.home() / ".pantheon" / ".env"
        console.print(f"\n[green]✓ API keys saved to {env_path}[/green]")
        if not standalone:
            console.print("  Starting Pantheon...\n")
    else:
        console.print(
            "\n[yellow]No API keys configured. "
            "Pantheon may not work correctly without provider keys.[/yellow]\n"
        )

    return configured_any


def _configure_fallback(console, pt_prompt) -> bool:
    console.print("\n[bold]Configure OpenAI-Compatible Fallback[/bold]")
    base_url = _prompt_value(
        pt_prompt,
        "LLM_API_BASE (e.g. https://your-openai-compatible-endpoint/v1): ",
    )
    api_key = _prompt_value(pt_prompt, "LLM_API_KEY: ", is_password=True)

    if base_url:
        _save_key_to_env_file("LLM_API_BASE", base_url)
        os.environ["LLM_API_BASE"] = base_url
        console.print("[green]✓ LLM_API_BASE saved[/green]")
    if api_key:
        _save_key_to_env_file("LLM_API_KEY", api_key)
        os.environ["LLM_API_KEY"] = api_key
        console.print("[green]✓ LLM_API_KEY saved[/green]")

    if not base_url and not api_key:
        console.print("[yellow]Nothing entered, skipped.[/yellow]")
        return False
    return True


def _configure_provider(entry: ProviderMenuEntry, console, pt_prompt) -> bool:
    console.print(f"\n[bold]Configure {entry.display_name}[/bold]")

    if entry.base_env_var:
        base_url = _prompt_value(
            pt_prompt,
            f"{entry.base_env_var} (optional): ",
        )
        if base_url:
            _save_key_to_env_file(entry.base_env_var, base_url)
            os.environ[entry.base_env_var] = base_url
            console.print(f"[green]✓ {entry.base_env_var} saved[/green]")

    api_key = _prompt_value(pt_prompt, f"{entry.env_var}: ", is_password=True)
    if not api_key:
        console.print("[yellow]Empty key, skipped.[/yellow]")
        return False

    _save_key_to_env_file(entry.env_var, api_key)
    os.environ[entry.env_var] = api_key
    console.print(f"[green]✓ {entry.env_var} saved[/green]")
    return True


def _prompt_value(pt_prompt, label: str, is_password: bool = False) -> str:
    try:
        value = pt_prompt(label, is_password=is_password)
    except (EOFError, KeyboardInterrupt):
        return ""
    return value.strip()


def _provider_is_configured(entry: ProviderMenuEntry) -> bool:
    return bool(get_settings().get_api_key(entry.env_var))


def _fallback_is_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.get_api_key("LLM_API_BASE") and settings.get_api_key("LLM_API_KEY")
    )


def _save_key_to_env_file(env_var: str, value: str):
    """Append or update a key in ~/.pantheon/.env."""
    env_dir = Path.home() / ".pantheon"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_file = env_dir / ".env"

    lines = []
    if env_file.exists():
        lines = env_file.read_text().splitlines()

    lines = [line for line in lines if not line.startswith(f"{env_var}=")]
    lines.append(f"{env_var}={value}")

    env_file.write_text("\n".join(lines) + "\n")


def _remove_key_from_env_file(env_var: str):
    """Remove a key from ~/.pantheon/.env and unset it from the current process."""
    env_file = Path.home() / ".pantheon" / ".env"
    if env_file.exists():
        lines = env_file.read_text().splitlines()
        lines = [line for line in lines if not line.startswith(f"{env_var}=")]
        env_file.write_text("\n".join(lines) + "\n" if lines else "")
    os.environ.pop(env_var, None)
