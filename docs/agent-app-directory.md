# The Agent's App directory

The Agent now reaches Apps through the `pantheonos_*` tools
(docs/pantheonos-app-interface.md); `call_app_tool` and `describe_app` below
are `pantheonos_call_app` and `pantheonos_describe_app`, and an App with only
a window is listed too. The rest of this document still holds.

Every App the owner has is reached by the Agent the same way: it is listed by
`call_app_tool`, described by `describe_app` and called with `call_app_tool`.
Before this, only the Apps of the Agent's deployment were (its toolsets); an
App installed in the library could only be reached through the Desktop's
`desktop_apps` and `app_call`, which called any method of any installed App
with the Desktop's authority and no schema.

## What an App offers the Agent

An App offers two kinds of capabilities, both declared in its manifest:

* **Tools**: backend methods in `provides.tools` that are not `hidden`, with
  their parameters. A tool runs in the App's backend process and needs no
  window.
* **Actions**: window actions in `actions` (name, description, parameters).
  An action runs in the App's window, in the owner's open desktop.

Most library Apps today offer only actions (Office, Cytoscape, Phylotree, the
viewers); a few offer tools. An App that declares neither is not listed.
Undeclared backend methods are never callable by the Agent.

## One directory

The directory lists, for each App: its id, name, one-line description, and
its tool and action names.

* The deployment's Apps are listed under their toolset names
  (`file_manager`, `shell`, `integrated_notebook`, …) as before.
* Library Apps are listed under their App ids, with separators as
  underscores (`game-of-life` is `game_of_life`) and a one-line description.
  An App that is also a deployment App is listed once, as the deployment
  toolset; its window stays reachable through the Desktop's window tools.
* Disabled Apps are left out.

The Desktop builds the library part from the installed manifests; it needs no
open desktop. The Agent takes one snapshot per conversation, on its first
model call, for the `call_app_tool` description, so the prompt prefix stays
cached. If the Desktop cannot answer yet, the Agent asks again a minute later.
An App installed later in the conversation is still found: naming it in
`describe_app` or `call_app_tool` reads the directory again.

## One call

`call_app_tool(app, tool, args)` routes by what the name is:

* a deployment toolset's tool: through its dependency grant, unchanged
  (`docs/app-dependency-rpc.md`);
* a library App's tool: the Desktop's `agent_app_call`. The Desktop checks
  that the tool and its arguments are declared, makes sure the App is
  installed and running on its default node (installing or starting it if
  needed), takes the Agent's lease (below) and calls the tool;
* an action: the Desktop's `agent_app_call` with the action. It uses a window
  of that App in the conversation's desktop, opening one if there is none,
  and runs the action there. Without an open desktop the call fails and says
  so; nothing else is done.

A call runs up to 600 s and, like every tool call, moves to the background
after 60 s.

The Desktop keeps its window tools (open, read, update, call actions in a
window the Agent opened, the browser, the Store, App development).
`app_call` is hidden from the model.

## Starting and stopping

An App starts when it is first used: the Agent calls it, or the owner opens
its window. It stops when nobody uses it:

* A call in progress and a lease keep an App in use. A window holds a lease
  while it is open. The Agent holds a lease per conversation, taken on each
  call and renewed by the Desktop until 10 minutes after that conversation's
  last call to the App.
* With no call and no lease, the node stops the App after a minute's grace.
  The App may decline (unsaved work); it is asked again later.

Each library App has a policy, set in its App settings:

| Policy | Effect |
|---|---|
| On demand (default) | Started when used, stopped when unused as above. |
| Resident | Kept running once started; never stopped for being idle. |
| Disabled | Not offered to the Agent; the owner can still open its window. |

The owner can stop an App in Fleet at any time; a later call starts it again,
so an App the Agent must not use is disabled. The deployment's Apps are
resident; the owner starts and stops them in Fleet → Agent deployment. The
Agent can stop a library App with `pantheonos_stop_app`, never a deployment
App.

The Agent's lease is kept by the Desktop process. If that process restarts,
the lease lapses within five minutes and the App is stopped when idle; the
next call starts it again.

## Authority

The Desktop acts for the owner, so it is the Desktop that limits what the
Agent may do with library Apps: only declared tools and actions, each with
only its declared parameters. Deployment toolsets keep their grants. Neither
path lets the Agent call an arbitrary method.
