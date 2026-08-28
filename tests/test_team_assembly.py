"""One agent that cannot be built must not cost the whole team.

Assembly is deliberately SERIAL — see the note in pantheon/factory/__init__.py.
Firing the toolset fetches together made a 10.017 s team take 18.798 s, because
the Endpoint they call is this same process and the cost is CPU, not the hop.
"""

import pytest

from pantheon.factory import create_agents_from_template


@pytest.mark.asyncio
async def test_one_agent_that_cannot_be_built_does_not_cost_the_team(monkeypatch):
    import pantheon.factory as factory

    async def fake_create_agent(endpoint_service, enable_mcp=True, **cfg):
        if cfg["name"] == "Researcher":
            raise RuntimeError("endpoint said no")
        return f"agent:{cfg['name']}"

    monkeypatch.setattr(factory, "create_agent", fake_create_agent)

    configs = {n: {"name": n} for n in ("Leader", "Researcher", "Writer")}
    agents = await create_agents_from_template(object(), configs)

    # Order is not cosmetic either: the first agent is the team's leader.
    assert agents == ["agent:Leader", "agent:Writer"]
