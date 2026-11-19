"""Telling the owner an answer is ready, on their own devices.

A run that took a while ends with the owner often somewhere else: the Agent
asks its Fleet (fleet_notify) to notify the devices they turned notifications
on in. A quick answer does not: whoever asked is still looking at it.
"""
import asyncio
import re
from urllib.parse import quote

from pantheonos.utils.log import logger

#: A run shorter than this is watched to its end; no notification.
MIN_SECONDS = 30
BODY = 160

_tasks: set = set()
_MARKUP = re.compile(r"```.*?```|`|\*\*|__|^#{1,6}\s+|^\s*[-*]\s+|!\[[^\]]*\]\([^)]*\)", re.S | re.M)
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def _plain(text: str) -> str:
    """A reply as one line of words: no code blocks, emphasis marks or link targets."""
    text = _LINK.sub(r"\1", _MARKUP.sub(" ", text))
    return " ".join(text.split())


def answer_notification(chat_id: str, name: str, response: dict | None, seconds: float, *,
                        stopped: bool = False) -> dict | None:
    """What to tell the owner when a conversation's run ends, or None.

    None for a short run, one the user stopped, and one with nothing to say.
    A failed run is told too: the owner is waiting for it either way.
    """
    if stopped or seconds < MIN_SECONDS or not isinstance(response, dict) or not chat_id:
        return None
    title = (name or "").strip() or "Your Agent"
    if response.get("success") is False:
        body = "Stopped with an error: " + _plain(str(response.get("message") or "see the conversation"))
    else:
        body = _plain(str(response.get("response") or ""))
        if not body:
            body = "The answer is ready."
    if len(body) > BODY:
        body = body[:BODY - 1].rstrip() + "…"
    return {"title": title[:100], "body": body, "url": f"/app/agent?chatId={quote(chat_id, safe='')}",
            "tag": f"agent.{chat_id}"[:64]}


def notify_owner(agent, notification: dict | None) -> "asyncio.Task | None":
    """Send it through the Agent's Fleet, in the background. None when there is
    nothing to send or this Agent reaches no Fleet. It never raises: a run's
    end does not depend on a notification."""
    from pantheon.agent_nodes import fleet_of

    fleet = fleet_of(agent) if agent is not None and notification else None
    if fleet is None:
        return None

    async def send():
        try:
            reply = await asyncio.wait_for(fleet.call_tool("fleet_notify", notification), timeout=30)
            if isinstance(reply, dict) and reply.get("success") is False:
                logger.info(f"The owner was not notified: {reply.get('error')}")
        except Exception as error:  # an older Fleet, the Hub away, notifications off
            logger.info(f"The owner was not notified: {error}")

    task = asyncio.create_task(send())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task
