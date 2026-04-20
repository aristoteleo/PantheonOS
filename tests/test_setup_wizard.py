from unittest.mock import patch


def test_setup_wizard_imports_without_custom_endpoint_types():
    from pantheon.repl import setup_wizard

    assert any(entry.provider_key == "openai" for entry in setup_wizard.PROVIDER_MENU)
    gemini_entry = next(entry for entry in setup_wizard.PROVIDER_MENU if entry.provider_key == "gemini")
    assert gemini_entry.base_env_var == "GEMINI_API_BASE"


def test_check_and_run_setup_skips_when_openai_fallback_is_configured(monkeypatch):
    monkeypatch.setenv("LLM_API_BASE", "https://fallback.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "fallback-key")
    monkeypatch.delenv("SKIP_SETUP_WIZARD", raising=False)

    from pantheon.repl import setup_wizard

    with patch.object(setup_wizard, "run_setup_wizard") as run_setup_wizard:
        setup_wizard.check_and_run_setup()

    run_setup_wizard.assert_not_called()


def test_check_and_run_setup_skips_when_legacy_custom_openai_is_configured(monkeypatch):
    monkeypatch.setenv("CUSTOM_OPENAI_API_BASE", "https://legacy.example/v1")
    monkeypatch.setenv("CUSTOM_OPENAI_API_KEY", "legacy-key")
    monkeypatch.delenv("SKIP_SETUP_WIZARD", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from pantheon.repl import setup_wizard

    with patch.object(setup_wizard, "run_setup_wizard") as run_setup_wizard:
        setup_wizard.check_and_run_setup()

    run_setup_wizard.assert_not_called()
