import pytest

from pantheon.repl.prompt_app import model_label


@pytest.mark.parametrize("model, label", [
    ("fleet-model://platform/openrouter%2Fdeepseek%2Fdeepseek-v4.1-flash", "DeepSeek V4.1 Flash"),
    ("fleet-model://platform/openrouter%2Fdeepseek%2Fdeepseek-v4.1-flash-1017", "DeepSeek V4.1 Flash"),
    ("openrouter/anthropic/claude-sonnet-4.6", "Claude Sonnet 4.6"),
    ("openrouter/anthropic/claude-fable-5.1:batch", "Claude Fable 5.1"),
    ("openai/gpt-5.5", "GPT 5.5"),
    ("fleet-route://tier-normal", "route tier-normal"),
    ("", ""),
])
def test_model_label_names_the_model_not_its_address(model, label):
    assert model_label(model) == label
