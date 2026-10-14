# The Agent's PantheonOS interface (2026-10-14)

The Agent reaches every App through one set of `pantheonos_*` tools. They
list, describe, call, start, stop, manage and develop Apps. A team template
says which Apps an agent may use with an `apps` filter. Sub-agents, background
tasks, task tracking, skills and memory are the Agent's own capabilities. They
are not Apps.

## Before

An Agent reached Apps three ways:

* **Deployment Apps** (files, shell, notebook, web, evolution, desktop, fleet,
  model services). These were its toolsets: listed by `call_app_tool` and read
  with `describe_app` (docs/agent-core.md).
* **Library Apps**. The Desktop listed them and the Agent called them through
  the same two tools (docs/agent-app-directory.md).
* **App management**. These were `desktop_*` tools of the Desktop App:
  * `desktop_store_apps`, `desktop_store_manage`, `desktop_store_git`
  * `desktop_app_develop`, `desktop_app_store`, `desktop_app_placement`
  * `desktop_app_install_on_node`, `desktop_app_build`, `desktop_app_launch_job`

  They were named after the Desktop, but they act on the owner's Apps.

A template's `toolsets` list did two things. It chose which deployment Apps
were bound to the agent, and it chose what the agent saw. Library Apps came
with `desktop`. Task tracking and skills showed up as the "Apps" `task` and
`skills`.

## Apps and built-ins

An **App** is anything the owner deployed or installed. It offers backend
tools, window actions, or both (docs/agent-app-directory.md). Some Apps have
only a window, some only a backend.

The deployment Apps, the library Apps and the MCP servers are all Apps to the
Agent. Each is named in one namespace:

* a deployment App by its toolset name (`file_manager`, `shell`, …);
* a library App by its id with separators as underscores (`game_of_life`);
* an MCP server by its server name.

A **built-in** is part of the Agent itself. Built-ins are always on, are not
listed as Apps, and are not affected by the `apps` filter:

| Built-in | Tools |
|---|---|
| Sub-agents | `call_agent` |
| Background tasks | `background_task` |
| Task tracking | `task_boundary`, `register_output`, `notify_user`, `list_outputs` |
| Skills | `skill_view`, `skill_manage` |
| Memory | none: files read and written with the file tools; relevant memories are added to the conversation automatically |

Built-in tools are declared to the model directly, with short descriptions.
Task tracking's descriptions are rewritten to fit (notify_user alone was
9.4 KB). Two kinds of skill tools fold into `skill_manage` actions:

* the skill Store tools (search, browse, adopt, rate, publish);
* `skill_list`. The skill index is already in the prompt.

## The tools

An agent declares:

* `read_file`, `write_file`, `update_file` (Files App) and `run_command`
  (Shell App). These are App tools declared directly because nearly every turn
  uses them. Each is declared only if its App passes the filter.
* the built-ins above;
* the seven `pantheonos_*` tools:

| Tool | Does |
|---|---|
| `pantheonos_list_apps(query?)` | The Apps this agent may use, read live. For each: name, description, source (deployment, library, MCP), what it offers (backend tools, window actions, a window), state (running, and on which node; or stopped), policy, and the names of its tools and actions. |
| `pantheonos_describe_app(app)` | One App in full: each tool's and action's description and JSON parameters, where it runs or can run, its current version and its development branches. |
| `pantheonos_call_app(app, tool, args)` | Calls a tool or window action, starting the App if needed (below). |
| `pantheonos_start_app(app, node?, version?, window?)` | Starts an App's backend on its default node or `node`, installing it there first if needed. `version` runs a tag, `branch:<name>` or commit, e.g. a development version. `window: true` opens a window of the App on the conversation's desktop. On an HPC cluster's login node, `job` (Slurm resources) runs it as a bounded job. |
| `pantheonos_stop_app(app, node?)` | Stops a library App's backend. The App may decline (unsaved work). |
| `pantheonos_manage_app(action, …)` | Installed Apps: `search` and `inspect` the Store, `install`, `uninstall` (to the Trash), `restore`, `versions`, `set_version` (the default version: a tag, a branch, or latest), `set_node` (the default node), `set_policy` (`on_demand` or `resident`). |
| `pantheonos_develop_app(action, app, …)` | App code: `create`, `fork`, `status`, `files`, `read`, `write`, `diff`, `branch`, `switch`, `merge`, `commit`, `test`, `history`, `build` (for a node's platform), `tag`, `publish`, `pull`, and contributions to a public repository (`submit`, `review`, `merge_contribution`). |

`pantheonos_call_app`'s description lists every App and its tool names. It is
taken once per conversation, so the prompt prefix stays cached, as with
`call_app_tool` before. An App installed later is still found, because naming
it reads the directory again. `pantheonos_list_apps` is the live view.

`pantheonos_develop_app` keeps a short description. The development workflow
(branch before editing, read before writing, tag then try the version, publish
only when asked) moves to a built-in skill, `app-development`, which the tool's
description points to.

### Start and stop

Calling an App still starts it when needed, and the Agent's lease still keeps
it running until ten minutes after its last call
(docs/agent-app-directory.md). `pantheonos_start_app` and
`pantheonos_stop_app` are for doing this explicitly:

* starting a development version to try it;
* starting on a chosen node or as an HPC job;
* opening a window;
* stopping a long-running App the Agent is done with.

A window of the App that is still open starts it again on its next call.

The Agent cannot stop a deployment App. Files, Shell and the others are what
the Agent works with; the owner starts and stops them in Fleet → Agent
deployment. `pantheonos_start_app` on a deployment App reports its state.

`set_policy` cannot set `disabled`. Disabling an App is how the owner keeps it
from the Agent, and a disabled App is not listed to the Agent at all.

## Where it runs

* **Agent side** (`pantheon/agent_pantheonos.py`): the seven tools, the App
  index and the `apps` filter.
* **Deployment Apps**: called through their dependency grants, unchanged
  (docs/app-dependency-rpc.md).
* **Library Apps and App management**: the Desktop App's process. It holds the
  library, the packages and the windows. Its App-management tools become
  hidden methods, called by the `pantheonos_*` tools and by the UI. They no
  longer appear among the Desktop's tools.
* **The Desktop's own tools** (windows, the browser, screenshots, and
  `desktop_apps`: which App opens which file type) stay the Desktop App's
  tools, reached with `pantheonos_call_app(app="desktop", …)` like any App's.

The Desktop's methods for the Agent (`agent_app_*`, granted to the Agent as
service methods in `pantheon/apps/general_agent_preset.py`):

| Method | Used by |
|---|---|
| `agent_app_directory(exclude, state)` | the App index; `pantheonos_list_apps` (state adds running backends) |
| `agent_app_describe(app_id)` | `pantheonos_describe_app` |
| `agent_app_call(app_id, name, args, holder, node, version)` | `pantheonos_call_app` |
| `agent_app_start(app_id, node, version, window, job, holder)` | `pantheonos_start_app` |
| `agent_app_stop(app_id, node, holder)` | `pantheonos_stop_app` |
| `agent_app_manage(action, app_id, args)` | `pantheonos_manage_app` |
| `agent_app_develop(action, app_id, args)` | `pantheonos_develop_app` |

`apps/desktop/agent_app_ops.py` maps each manage and develop action to the
Desktop operation the UI uses for it.

The authority is unchanged:

* deployment Apps keep their allocator grants;
* for library Apps, the Desktop allows only declared tools and actions, each
  with only its declared parameters.

The `apps` filter narrows what one agent sees and calls. It grants nothing.

## The template field `apps`

```yaml
apps:
  allow: [file_manager, shell, web]   # only these (omitted: every App)
  deny: [evolution]                   # never these
```

* No `apps` field: the agent may use every App.
* The names are those `pantheonos_list_apps` shows.
* The filter applies to every way the agent can reach an App:
  * the App tools declared directly;
  * the App index;
  * the seven `pantheonos_*` tools;
  * which deployment Apps are bound to the agent. Each allowed deployment App
    is bound lazily, on first use, as toolsets are bound now.
* Each sub-agent profile has its own filter.
* An agent that names neither `apps` nor App toolsets may use every App
  (toolsets that name only built-ins or MCP servers name no App).
* A template that still says `toolsets` reaches exactly what it reaches today:
  * the deployment Apps it lists, plus the deployment's default ones;
  * library Apps when it lists `desktop`;
  * `task`, `think` and `skills` are ignored, because they are built-ins;
  * `mcp` and `mcp:<name>` select MCP servers, as before.

  The editor saves `apps` and drops `toolsets`.

Factory templates:

* The General Team leader has no filter.
* `worker` allows `file_manager`, `shell`, `integrated_notebook`, `web`.
* `researcher` and `scientific_illustrator` allow `file_manager`, `web`.

## History

History keeps an App call in its expanded form (`integrated_notebook__
notebook_execute` with its own arguments). The model's view folds it into
`pantheonos_call_app`. A recorded `call_app_tool` call from an older
conversation folds the same way.

## UI

* **Template editor**: the agent's toolset list becomes an **Apps** control:
  All Apps, Only these, or All except these, over the owner's Apps.
* **Agent detail panel**: shows the filter.

## Not in this change

* Moving App management out of the Desktop process into a system App of its
  own.
* Listing MCP servers in the Store.
