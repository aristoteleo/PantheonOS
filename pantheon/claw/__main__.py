from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence

import fire
from dotenv import load_dotenv

from .config import ALL_CHANNELS, ClawConfigStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pantheonclaw",
        description="PantheonClaw gateway-first CLI for mobile channel integration.",
    )
    parser.add_argument(
        "--show-default-config",
        action="store_true",
        help="Print the current claw config template and exit.",
    )
    parser.add_argument(
        "--config-path",
        action="store_true",
        help="Print the PantheonClaw config path and exit.",
    )
    parser.add_argument(
        "--list-channels",
        action="store_true",
        help="Print the known channel names and exit.",
    )
    parser.add_argument(
        "--qq-app-id",
        help="Persist QQ app id into PantheonClaw config before startup.",
    )
    parser.add_argument(
        "--qq-client-secret",
        help="Persist QQ client secret into PantheonClaw config before startup.",
    )
    return parser


def _normalize_argv(argv: Sequence[str] | None) -> list[str]:
    return list(sys.argv[1:] if argv is None else argv)


def _prepare_runtime() -> None:
    load_dotenv()
    load_dotenv(
        os.path.join(os.path.expanduser("~"), ".pantheon", ".env"),
        override=False,
    )
    load_dotenv(
        os.path.join(os.path.expanduser("~"), ".env"),
        override=False,
    )
    os.environ.setdefault("PANTHEON_LAUNCHER", "pantheonclaw")
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def _run_gateway(argv: Sequence[str] | None = None) -> int:
    _prepare_runtime()
    from pantheon.repl.setup_wizard import check_and_run_setup
    from pantheon.chatroom.start import start_services

    check_and_run_setup()
    fire.Fire(start_services, command=_normalize_argv(argv), name="pantheonclaw")
    return 0


def _run_cli(argv: Sequence[str] | None = None) -> int:
    _prepare_runtime()
    from pantheon.repl.setup_wizard import check_and_run_setup
    from pantheon.repl.__main__ import start as repl_start

    check_and_run_setup()
    fire.Fire(repl_start, command=_normalize_argv(argv), name="pantheonclaw")
    return 0


def _run_store(argv: Sequence[str] | None = None) -> int:
    _prepare_runtime()
    from pantheon.store.cli import StoreCLI

    fire.Fire(StoreCLI, command=_normalize_argv(argv), name="pantheonclaw")
    return 0


def _run_setup() -> int:
    _prepare_runtime()
    from pantheon.repl.setup_wizard import run_setup_wizard

    run_setup_wizard(standalone=True)
    return 0


def _apply_cli_overrides(store: ClawConfigStore, args: argparse.Namespace) -> None:
    if not args.qq_app_id and not args.qq_client_secret:
        return
    cfg = store.load()
    qq_cfg = dict(cfg.get("qq") or {})
    if args.qq_app_id:
        qq_cfg["app_id"] = args.qq_app_id
    if args.qq_client_secret:
        qq_cfg["client_secret"] = args.qq_client_secret
    cfg["qq"] = qq_cfg
    store.save(cfg)


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = _normalize_argv(argv)
    parser = build_parser()
    args, remainder = parser.parse_known_args(raw_argv)
    store = ClawConfigStore()
    _apply_cli_overrides(store, args)

    if args.config_path:
        print(store.path)
        return 0
    if args.list_channels:
        print("\n".join(ALL_CHANNELS))
        return 0
    if args.show_default_config:
        print(json.dumps(store.load_masked(), indent=2, ensure_ascii=False))
        return 0

    if remainder:
        subcommand = remainder[0]
        if subcommand == "ui":
            return _run_gateway(remainder[1:])
        if subcommand == "cli":
            return _run_cli(remainder[1:])
        if subcommand == "store":
            return _run_store(remainder[1:])
        if subcommand == "setup":
            return _run_setup()

    # Bare invocation and unknown flags both fall through to gateway mode.
    return _run_gateway(remainder)


if __name__ == "__main__":
    raise SystemExit(main())
