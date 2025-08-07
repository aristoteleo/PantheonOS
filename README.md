<div align="center">
  <h1> Pantheon </h1>

  <p> A framework for building distributed LLM based multi-agent system. </p>

  <p>
    <a href="https://github.com/aristoteleo/pantheon-agents/actions/workflows/test.yml">
        <img src="https://github.com/aristoteleo/pantheon-agents/actions/workflows/test.yml/badge.svg" alt="Build Status">
    </a>
    <a href="https://pypi.org/project/pantheon-agents/">
      <img src="https://img.shields.io/pypi/v/pantheon-agents.svg" alt="Install with PyPi" />
    </a>
    <a href="https://github.com/aristoteleo/pantheon-agents/blob/master/LICENSE">
      <img src="https://img.shields.io/github/license/aristoteleo/pantheon-agents" alt="MIT license" />
    </a>
  </p>
</div>


**Work In Progress**


## Installation

```bash
pip install pantheon-agents
```

## Examples

See the [examples](examples) folder for more advanced usage patterns and custom implementations.


## TODO

- [x] REPL for meeting and agent
- [x] Reasoning with O1 / Gemini Flash Thinking / Deepseek-R1
- [x] Distributed
  + [x] Tools
  + [ ] Agents
    * [x] Support streaming response
    * [ ] Support use in team
- [x] Agent team
  + [x] Sequential Team
  + [x] Swarm Team
  + [x] MoA(Mixture-of-Agents) Team
- [x] Memory persistence
- [ ] Report generation
- [x] Vision ability
- [ ] GUI
  + [x] Web UI
  + [x] Slack
- [ ] Documentation

## Start a chatroom and work with it

```bash
git clone https://github.com/aristoteleo/pantheon-agents.git
cd pantheon-agents

pip install -e .
export OPENAI_API_KEY=your_openai_api_key
python -m pantheon.chatroom
```

Then you can see a service id of chatroom, you can copy it, then open https://pantheon-ui.vercel.app/ paste the service id and click "Connect".

## Pantheon CLI

The easiest way to start using Pantheon is through the CLI interface:

```bash
# Start with default settings
python -m pantheon.cli
```

See [pantheon/cli/README.md](pantheon/cli/README.md) for detailed documentation.
