from io import StringIO
from types import SimpleNamespace

from rich.console import Console


def test_keys_command_lists_gemini_base_url(monkeypatch):
    from pantheon.repl.core import Repl

    for key in (
        "LLM_API_BASE",
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_API_BASE",
        "GEMINI_API_KEY",
        "GEMINI_API_BASE",
    ):
        monkeypatch.delenv(key, raising=False)

    buffer = StringIO()
    repl = Repl.__new__(Repl)
    repl._output = SimpleNamespace(
        console=Console(file=buffer, force_terminal=False, color_system=None)
    )

    Repl._handle_keys_command(repl, "")
    output = buffer.getvalue()

    assert "GEMINI_API_BASE" in output
    assert "/keys <number|name> <base_url> <api_key>" in output
    assert "OpenAI/Anthropic only" not in output
