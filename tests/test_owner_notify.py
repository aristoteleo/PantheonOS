"""The Agent tells the owner's devices when a run that took a while ends."""
import asyncio
from types import SimpleNamespace as NS

from pantheon.chatroom.owner_notify import MIN_SECONDS, answer_notification, notify_owner


def test_a_long_run_is_told_with_the_start_of_its_answer():
    told = answer_notification("chat 1/a", "Figure for the paper", {"success": True, "response":
                               "## Done\n\nThe **figure** is in `out/fig1.png`. See [the notes](notes.md).\n```py\nprint(1)\n```"}, 95)
    assert told == {"title": "Figure for the paper", "body": "Done The figure is in out/fig1.png . See the notes.",
                    "url": "/app/agent?chat=chat%201%2Fa", "tag": "agent.chat 1/a"}


def test_a_short_run_a_stopped_one_and_no_answer_are_not_told():
    ok = {"success": True, "response": "Done."}
    assert answer_notification("c1", "Notes", ok, MIN_SECONDS - 1) is None
    assert answer_notification("c1", "Notes", ok, 300, stopped=True) is None
    assert answer_notification("c1", "Notes", None, 300) is None
    assert answer_notification("", "Notes", ok, 300) is None


def test_a_failed_run_an_empty_answer_and_a_long_one():
    assert answer_notification("c1", "", {"success": False, "message": "The model did not answer"}, 40) == {
        "title": "Your Agent", "body": "Stopped with an error: The model did not answer",
        "url": "/app/agent?chat=c1", "tag": "agent.c1"}
    assert answer_notification("c1", "Notes", {"success": True, "response": "  "}, 40)["body"] == "The answer is ready."
    body = answer_notification("c1", "Notes", {"success": True, "response": "word " * 200}, 40)["body"]
    assert len(body) <= 160 and body.endswith("…")


def test_it_goes_through_the_agents_fleet_and_never_raises():
    async def run():
        calls = []

        async def call_tool(name, args):
            calls.append((name, args))
            if args.get("title") == "boom":
                raise RuntimeError("Method fleet_notify not found on service fleet")
            return {"success": True, "devices": 1, "sent": 1}

        agent = NS(providers={"fleet": NS(call_tool=call_tool)})
        told = {"title": "Notes", "body": "Done.", "url": "/app/agent?chat=c1", "tag": "agent.c1"}
        await notify_owner(agent, told)
        await notify_owner(agent, {**told, "title": "boom"})  # an older Fleet: logged, not raised
        assert [c[0] for c in calls] == ["fleet_notify", "fleet_notify"] and calls[0][1] == told
        assert notify_owner(agent, None) is None
        assert notify_owner(NS(providers={}), told) is None  # no Fleet: nothing to ask
    asyncio.run(run())
