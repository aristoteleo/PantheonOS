"""The General Team: the set of Apps the platform sets up for an owner.

The Agent with its tool providers, the dependency allocator and the model
connector, composed from the owner's choices. The App framework
(pantheonos.appkit) knows nothing of it; this package uses the framework to
describe one particular deployment of it:

* team: the General Team's setup (Agent tiers, platform-budget models).
* agent_preset: the Agent's dependency wiring from the owner's choices.
* agent_defaults, agent_deployment: the owner's default Agent dependencies
  and the generic deployment recipe they become.
* release_profile: the General Team compiled into a release set's
  profile.json, which the Fleet controller deploys.
* local_agent: the local product's bundle and setup (`pantheon local`).

None of it imports the Agent runtime; the Agent is named as an App.
"""
