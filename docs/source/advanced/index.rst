Advanced Topics
===============

Deep-dive guides for power users and developers who want to push Pantheon beyond the defaults.

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Topic
     - Level
     - What You Will Learn
   * - :doc:`evolution`
     - Advanced
     - LLM-guided MAP-Elites optimization; ``EvolutionTeam``; custom evaluators; multi-island search; visualizing results
   * - :doc:`distributed`
     - Advanced
     - NATS hub topology; multi-worker HA; remote endpoints; Docker/K8s deployment
   * - :doc:`learning`
     - Intermediate
     - Skillbook system; automatic skill recording; retrieval and refinement; sharing skills across a team
   * - :doc:`extending`
     - Advanced
     - Custom toolsets, team patterns, providers, and plugins

Evolution System
----------------

The Evolution system runs MAP-Elites + LLM mutation to automatically improve code.
See :doc:`evolution` for the full guide.

See also :doc:`/toolsets/evolution` for using the EvolutionToolSet inside an agent,
and the ``examples/evolution_*/`` directories in the repository for runnable examples.

Distributed Deployment
----------------------

Pantheon's NATS backend supports multi-machine deployments where agents, toolsets, and
endpoints run on separate processes or machines. See :doc:`distributed`.

For multi-machine agent control via the Fleet system, see :doc:`/toolsets/fleet`
and :doc:`/interfaces/ui/fleet`.

Learning System
---------------

Agents can record successful strategies as reusable skills and retrieve them in future
sessions. See :doc:`learning`.

Extending Pantheon
------------------

Build custom toolsets, register new team patterns, or add third-party providers.
See :doc:`extending`.
