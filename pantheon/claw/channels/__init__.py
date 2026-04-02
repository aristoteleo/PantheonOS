from importlib import import_module

_RUNNERS = {
    "run_discord_channel": ("pantheon.claw.channels.discord", "run_discord_channel"),
    "run_slack_channel": ("pantheon.claw.channels.slack", "run_slack_channel"),
    "run_telegram_channel": ("pantheon.claw.channels.telegram", "run_telegram_channel"),
}


def __getattr__(name: str):
    if name not in _RUNNERS:
        raise AttributeError(name)
    module_name, attr_name = _RUNNERS[name]
    module = import_module(module_name)
    return getattr(module, attr_name)

__all__ = [
    "run_discord_channel",
    "run_slack_channel",
    "run_telegram_channel",
]
