import os
import asyncio
from typing import Callable

from slack_bolt.app.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler

from ..agent import Agent
from ..team import Team
from ..utils.log import logger


async def run_app(
        agent_factory: Callable[[], Agent | Team] | None = None,
        app_token: str | None = None,
        bot_token: str | None = None,
        daily_message_limit_per_user: int = 50,
        ):

    if agent_factory is None:
        agent_factory = lambda: Agent(
            "slack-assistant",
            "You are a helpful assistant that can answer questions and help with tasks.",
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
    agents = {}

    app = AsyncApp(token=bot_token)

    def is_direct_message(body):
        return body["event"]["channel"].startswith("D")

    def get_agent(body):
        user_id = body["event"]["user"]
        agent = agents.get(user_id)
        if agent is None:
            agent = agent_factory()
            agents[user_id] = agent
        return agent

    async def get_reply(body):
        content = body["event"]["text"]
        user_id = body["event"]["user"]

        agent = get_agent(body)
        if content.startswith("!toolset"):
            await agent.remote_toolset(content.split(" ")[1])
            return "Toolset successfully loaded."

        user_name = await get_user_name(user_id)
        if user_name:
            content = f"{user_name}: {content}"
        user_message_count[user_id] = user_message_count.get(user_id, 0) + 1
        if user_message_count[user_id] > daily_message_limit_per_user:
            return "You have reached the daily message limit."

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

        task = asyncio.create_task(get_reply(body))
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

    @app.event("message")
    async def handle_message(body, say, client, ack):
        logger.info(body)
        if is_direct_message(body):
            await response_user(body, client, ack, in_thread=False)
        else:
            if body["event"].get("thread_ts"):
                await response_user(body, client, ack, in_thread=True)

    @app.event("app_mention")
    async def handle_app_mention(body, say, client, ack):
        logger.info(body)
        await response_user(body, client, ack, in_thread=True)

    async def reset_user_message_count():
        while True:
            await asyncio.sleep(86400)
            user_message_count.clear()

    handler = AsyncSocketModeHandler(app, app_token)
    await asyncio.gather(
        handler.start_async(),
        reset_user_message_count(),
    )
