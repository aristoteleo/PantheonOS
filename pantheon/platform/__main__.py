"""Run the platform RPC host using the configured service bus and Fleet."""

import argparse
import asyncio

from .service import PlatformService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id-hash", required=True,
                        help="Stable deployment identity, distinct from the Agent service")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--workspace", help="Home project directory (defaults to launch cwd)")
    args = parser.parse_args()
    asyncio.run(PlatformService(id_hash=args.id_hash, workspace_path=args.workspace)
                .run(log_level=args.log_level))


if __name__ == "__main__":
    main()
