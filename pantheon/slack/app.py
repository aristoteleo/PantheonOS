import os
import asyncio
import tempfile
from typing import Callable

import aiohttp
from rich.console import Console
from slack_bolt.app.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler

from ..agent import Agent
from ..team import Team
from ..utils.log import logger
from ..utils.vision import vision_input
from ..utils.misc import print_agent_message


async def run_app(
        agent_factory: Callable[[], Agent | Team] | None = None,
        app_token: str | None = None,
        bot_token: str | None = None,
        daily_message_limit_per_user: int = 50,
        ):

    if agent_factory is None:
        agent_factory = lambda: Agent(
            "slack-assistant",
            "You are a helpful slack assistant. Keep your responses compatible with slack formatting.",
            model="gpt-4o-mini"
        )

    try:
        if app_token is None:
            app_token = os.environ["SLACK_APP_TOKEN"]
        if bot_token is None:
            bot_token = os.environ["SLACK_BOT_TOKEN"]
    except Exception:
        logger.error(
            "Error getting Slack tokens and environment variables. "
            "Please set the SLACK_APP_TOKEN and SLACK_BOT_TOKEN environment variables."
        )
        return

    user_message_count = {}
    console = Console()
    agents = {}
    events_queue = asyncio.Queue()

    app = AsyncApp(token=bot_token)

    async def download_file(url, filename):
        headers = {"Authorization": f"Bearer {bot_token}"}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    with open(filename, "wb") as f:
                        f.write(await resp.read())
                    print(f"✅ download file: {filename}")
                    return filename
                else:
                    print(f"❌ download file failed, status: {resp.status}")
                    return None

    async def collect_events(agent_id):
        while True:
            msg = await agents[agent_id].events_queue.get()
            events_queue.put_nowait((agent_id, msg))

    async def create_agent(id):
        agent = agent_factory()
        agents[id] = agent
        loop = asyncio.get_event_loop()
        loop.create_task(collect_events(id))
        return agent

    def is_direct_message(body):
        return body["event"]["channel"].startswith("D")

    async def get_agent(body):
        user_id = body["event"]["user"]
        if is_direct_message(body):
            agent = agents.get(user_id)
            if agent is None:
                agent = await create_agent(user_id)
            return agent
        else:
            channel_id = body["event"]["channel"]
            if channel_id not in agents:
                agents[channel_id] = await create_agent(channel_id)
            return agents[channel_id]
    
    def reset_agent(body):
        user_id = body["event"]["user"]
        if is_direct_message(body):
            agents[user_id] = agent_factory()
        else:
            channel_id = body["event"]["channel"]
            agents[channel_id] = agent_factory()

    async def get_reply(body):
        content = body["event"]["text"]
        user_id = body["event"]["user"]

        agent = await get_agent(body)
        if content.startswith("!toolset"):
            await agent.remote_toolset(content.split(" ")[1])
            return "Toolset successfully loaded."
        elif content.startswith("!reset"):
            reset_agent(body)
            return "Agent reset."
        elif content.startswith("!clear"):
            agent.memory.clear()
            return "Agent memory cleared."
        elif content.startswith("!help"):
            from .. import __version__
            return f"""You can talk with me in a direct message or in a channel. Run following commands in a message to control me:

`!reset` - Reset me to default state, it will forget all memory and toolset.
`!toolset <service_id/service_name>` - Load a toolset.
`!clear` - Clear my memory.
`!help` - Show this help.

How to start a toolset in your local computer:

1. Install `pantheon-agents[tool]` Python package: `pip install pantheon-agents[tool]`
2. Start the toolset(for example, a python interpreter): `python -m pantheon.tools.python`
3. Get the service id from the toolset's log.
4. Paste the service id and the command to load the toolset to me. For example: `!toolset your_service_id`

This project is still under development. If you have any feedback, you can raise an issue on the repo: https://github.com/aristoteleo/pantheon-agents

Current version: {__version__}
"""

        user_name = await get_user_name(user_id)
        if user_name:
            content = f"{user_name}: {content}"
        user_message_count[user_id] = user_message_count.get(user_id, 0) + 1
        if user_message_count[user_id] > daily_message_limit_per_user:
            return "You have reached the daily message limit."

        images = []
        if files := body["event"].get("files"):
            for file in files:
                url = file.get("url_private")
                if file.get("mimetype").startswith("image/"):
                    filename = await download_file(url, tempfile.NamedTemporaryFile().name)
                    images.append(filename)
        if images:
            content = vision_input(content, images, from_path=True)
        res = await agent.run(content)
        return res.content

    async def get_user_name(user):
        try:
            user_info = (await app.client.users_info(user=user))["user"]
            logger.info(f"user_info: {user_info}")
            return user_info["real_name"]
        except Exception:
            logger.warning(f"Failed to get user name for {user}")
            return None

    async def response_user(
            body, client, ack,
            in_thread=False, thinking_timeout=1.0):
        await ack()

        async def _post_message(msg):
            if in_thread:
                resp = await client.chat_postMessage(
                    channel=body["event"]["channel"],
                    text=msg,
                    thread_ts=body["event"]["ts"],
                )
            else:
                resp = await client.chat_postMessage(
                    channel=body["event"]["channel"],
                    text=msg,
                )
            return resp

        async def _get_reply():
            try:
                return await get_reply(body)
            except Exception as e:
                logger.error(f"Error getting reply: {e}")
                return "Sorry, I encountered an error. Please try again later."

        task = asyncio.create_task(_get_reply())
        done, _ = await asyncio.wait({task}, timeout=thinking_timeout)
        if task in done:
            res = await task
            resp = await _post_message(res)
        else:
            resp = await _post_message(":thinking_face: Thinking...")
            res = await task
            await client.chat_update(
                channel=body["event"]["channel"],
                text=res,
                ts=resp["ts"],
            )

    async def app_is_in_thread(app_id, channel_id, thread_id):
        thread_messages = await app.client.conversations_replies(
            channel=channel_id,
            ts=thread_id,
        )
        for message in thread_messages["messages"]:
            if message.get("app_id") == app_id:
                return True
        return False

    @app.event("message")
    async def handle_message(body, say, client, ack):
        logger.info(body)
        if is_direct_message(body):
            await response_user(body, client, ack, in_thread=False)
        else:
            event = body["event"]
            if thread_id := event.get("thread_ts"):
                if await app_is_in_thread(body['api_app_id'], event["channel"], thread_id):
                    await response_user(body, client, ack, in_thread=True)

    @app.event("app_mention")
    async def handle_app_mention(body, say, client, ack):
        logger.info(body)
        event = body["event"]
        if thread_id := event.get("thread_ts"):
            if await app_is_in_thread(body['api_app_id'], event["channel"], thread_id):
                return
        await response_user(body, client, ack, in_thread=True)

    async def reset_user_message_count():
        while True:
            await asyncio.sleep(86400)
            user_message_count.clear()

    async def print_tool_messages():
        while True:
            id, event = await events_queue.get()
            print_agent_message(f"agent({id})", event, console=console)

    handler = AsyncSocketModeHandler(app, app_token)
    await asyncio.gather(
        handler.start_async(),
        reset_user_message_count(),
        print_tool_messages(),
    )
