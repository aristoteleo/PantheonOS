"""Run the platform RPC host using the configured service bus and Fleet."""

import argparse
import asyncio
import os
import hashlib

from .service import PlatformService
from .bootstrap import legacy_agent_command, platform_seed, serve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--id-hash", help="Explicit platform service seed")
    identity.add_argument("--deployment-id", help="User deployment identity; derives a distinct platform seed")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--workspace", help="Home project directory (defaults to launch cwd)")
    parser.add_argument('--owner-state-directory', default=os.environ.get('PANTHEON_PLATFORM_STATE_DIR'),
                        help='Persistent private directory for platform operation journals; independent of process HOME')
    parser.add_argument("--app-preset", default=os.environ.get('PANTHEON_APP_PRESET'),
                        help="Owner-private ordinary App deployment recipe; progresses independently of platform readiness")
    parser.add_argument("--app-preset-url", default=os.environ.get('PANTHEON_APP_PRESET_URL'),
                        help="Fetch this workspace's owner-approved startup recipe from its paired Hub")
    parser.add_argument("--legacy-agent", action="store_true",
                        help="Temporarily launch the legacy Agent as an independent child")
    parser.add_argument('--model-budget-hub', help='Explicit paired HTTPS Hub for budget preparation requested by an App preset')
    parser.add_argument('--model-budget-token-file', help='Owner-private full Hub login file, never an App recipe value')
    parser.add_argument("agent_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.legacy_agent and not args.deployment_id:
        parser.error("--legacy-agent requires --deployment-id")
    if args.app_preset and args.app_preset_url:
        parser.error('Choose --app-preset or --app-preset-url, not both')
    if bool(args.model_budget_hub) != bool(args.model_budget_token_file):
        parser.error('Supply both --model-budget-hub and --model-budget-token-file')
    agent_args = args.agent_args
    if agent_args[:1] == ["--"]:
        agent_args = agent_args[1:]
    if agent_args and not args.legacy_agent:
        parser.error("Agent arguments require --legacy-agent")
    seed = args.id_hash or platform_seed(args.deployment_id)
    source = None
    if args.app_preset_url:
        from .app_preset import fetch_hub_preset
        user = os.environ.get('USER_ID') or args.deployment_id
        async def source():
            return await fetch_hub_preset(args.app_preset_url,
                hub=os.environ.get('PANTHEON_HUB_URL', ''), token=os.environ.get('FLEET_KEY', ''),
                owner='f_' + hashlib.sha256(user.encode()).hexdigest()[:16] if user else '')
    preparer = None
    if args.model_budget_hub:
        from pantheon.models.platform_budget import BudgetCredentialPreparer
        preparer = BudgetCredentialPreparer(hub=args.model_budget_hub, token_file=args.model_budget_token_file)
    service = PlatformService(id_hash=seed, workspace_path=args.workspace, app_preset=args.app_preset,
                              app_preset_source=source, model_credential_preparer=preparer,
                              owner_state_directory=args.owner_state_directory)
    command = legacy_agent_command(args.deployment_id, agent_args) if args.legacy_agent else None
    asyncio.run(serve(service, log_level=args.log_level, agent_command=command))


if __name__ == "__main__":
    main()
