"""Run the platform RPC host using the configured service bus and Fleet."""

import argparse
import asyncio
import os

from .service import PlatformService
from .bootstrap import legacy_agent_command, platform_seed, serve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--id-hash", help="Explicit platform service seed")
    identity.add_argument("--deployment-id", help="User deployment identity; derives a distinct platform seed")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--workspace", help="Home project directory (defaults to launch cwd)")
    parser.add_argument("--app-preset", default=os.environ.get('PANTHEON_APP_PRESET'),
                        help="Owner-private ordinary App deployment recipe; progresses independently of platform readiness")
    parser.add_argument("--legacy-agent", action="store_true",
                        help="Temporarily launch the legacy Agent as an independent child")
    parser.add_argument("agent_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.legacy_agent and not args.deployment_id:
        parser.error("--legacy-agent requires --deployment-id")
    agent_args = args.agent_args
    if agent_args[:1] == ["--"]:
        agent_args = agent_args[1:]
    if agent_args and not args.legacy_agent:
        parser.error("Agent arguments require --legacy-agent")
    seed = args.id_hash or platform_seed(args.deployment_id)
    service = PlatformService(id_hash=seed, workspace_path=args.workspace, app_preset=args.app_preset)
    command = legacy_agent_command(args.deployment_id, agent_args) if args.legacy_agent else None
    asyncio.run(serve(service, log_level=args.log_level, agent_command=command))


if __name__ == "__main__":
    main()
