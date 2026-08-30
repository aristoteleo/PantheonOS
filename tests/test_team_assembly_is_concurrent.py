"""Assembling a team must not be a queue of round trips.

Each agent's toolset providers fetch that toolset's schema from the Endpoint,
and the agents do not depend on each other. Done serially this was the largest
single cost of a cold boot — 6.686 s across three agents on staging, against
0.002 s once the schemas were cached, so it is round trips, not work.
"""

import asyncio

import pytest

from pantheon.factory import create_agents_from_template


@pytest.mark.asyncio
async def test_agents_are_built_concurrently_and_stay_in_order(monkeypatch):
    import pantheon.factory as factory

    running = 0
    peak = 0

    async def fake_create_agent(enable_mcp=True, **cfg):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)          # stand in for the round trip
        running -= 1
        return f"agent:{cfg['name']}"

    monkeypatch.setattr(factory, "create_agent", fake_create_agent)

    configs = {n: {"name": n} for n in ("Leader", "Researcher", "Writer")}
    agents = await create_agents_from_template(configs)

    assert peak == 3, "the three agents were built one after another"
    # Order is not cosmetic: the first agent is the team's leader.
    assert agents == ["agent:Leader", "agent:Researcher", "agent:Writer"]


@pytest.mark.asyncio
async def test_one_agent_that_cannot_be_built_does_not_cost_the_team(monkeypatch):
    import pantheon.factory as factory

    async def fake_create_agent(enable_mcp=True, **cfg):
        if cfg["name"] == "Researcher":
            raise RuntimeError("endpoint said no")
        return f"agent:{cfg['name']}"

    monkeypatch.setattr(factory, "create_agent", fake_create_agent)

    configs = {n: {"name": n} for n in ("Leader", "Researcher", "Writer")}
    agents = await create_agents_from_template(configs)

    assert agents == ["agent:Leader", "agent:Writer"]
