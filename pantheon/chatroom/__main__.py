import asyncio
import os
import sys

import fire
from dotenv import load_dotenv

# Load .env file with override=False to NOT override existing environment variables
# This allows command-line args (like --auto-start-nats) to take precedence
load_dotenv(override=False)
# Also load global API keys from ~/.pantheon/.env and ~/.env (legacy fallback)
load_dotenv(os.path.join(os.path.expanduser("~"), ".pantheon", ".env"), override=False)
load_dotenv(os.path.join(os.path.expanduser("~"), ".env"), override=False)

# Now safe to import other modules
from pantheon.chatroom.start import start_services
from pantheon.repl.setup_wizard import check_and_run_setup


if __name__ == "__main__":
    # Check for API keys and run setup wizard if none found
    check_and_run_setup()
    # prompt_toolkit may close the event loop; ensure one exists for Fire + async
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    if len(sys.argv) == 1 or (len(sys.argv) > 1 and sys.argv[1].startswith("-")):
        sys.argv.insert(1, "start")
    fire.Fire(
        {"start": start_services},
        name="pantheon-chatroom",
    )
