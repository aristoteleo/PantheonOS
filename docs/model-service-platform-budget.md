# Platform budget through the existing Model Service App

This is opt-in owner-side provisioning for Agent App extraction. It does not
change the default Atrium/CLI routing or migrate a user's active budget setting.
The existing LiteLLM service remains the upstream and the authority for the
user's key, limits and spending. No new budget account or inference server is
introduced.

## Provision on the selected node

Use a Fleet CLI containing `credentials ensure` and the matching Hub update to
`GET /api/users/me/llm-proxy`. The Hub response adds `fleet_id` and `api_base_url`
while retaining its existing browser fields. It requires the full owner login;
Fleet, Store and App-instance workload tokens cannot export the virtual key.

On the chosen node, supply that explicitly paired Hub login as a bounded,
owner-private token file (mode 0600). Do not put the token in a command argument
or shell history. The current command requires POSIX. For example, with actual
owner/node identities and local paths substituted:

```sh
python -m pantheon.models.platform_budget \
  --hub https://YOUR-PAIRED-HUB \
  --token-file /absolute/private/hub-login-token \
  --fleet-executable /absolute/path/to/fleet \
  --state-dir /absolute/path/to/fleet-state \
  --owner f_YOUR_FLEET_ID --node-id YOUR_NODE_ID \
  --ref node-secret://platform-budget \
  --output /absolute/private/budget-connector.json
```

The helper compares the authenticated Hub user's Fleet identity with the selected
vault owner and verifies the local node identity. It reuses the Hub's existing
`ensure_user_key` operation, then sends the virtual key through stdin to Fleet's
existing `credentials ensure`. Repeating with the same reference, endpoint and
key does not replace the stored credential. A changed endpoint/key conflicts;
rotation/recovery must be explicit. HTTP redirects, ambient proxy credentials,
unbounded responses and cross-owner responses are rejected.

The output contains only protocol, owner/node, `source: platform-budget`, the
Hub model mode, and a normal `connector` configuration:

```json
{
  "engine": "api",
  "endpoint": "https://YOUR-PAIRED-HUB/litellm/v1",
  "secret_ref": "node-secret://platform-budget"
}
```

It contains neither the Hub login token nor the virtual key. Keep the full-owner
login out of all App recipes. The Connector reads the node reference through the
existing restricted Fleet credential pipe. Provisioning does not install/start
Apps, publish models, or make a paid model request.

## Connect it using ordinary Model Services

Use the existing owner operation `model_services_attach`, with an explicit
`deployment_id`, display `name`, the descriptor's `node_id`, and the three fields
from `descriptor.connector`. This is the normal `ModelServiceManager.attach`
path; there is no budget-specific engine, lifecycle or inference implementation.
Then use `model_services_discover` and the existing publication controls to
select model IDs/capabilities for Agent access. Do not infer capabilities or
rename models from the Hub's mode: both `direct` and `openrouter` use the model
IDs the proxy actually serves.

In the Agent deployment recipe, authorize this deployment's exact binding in
the existing `model-access` policy and bind `agent.models.model_services` to the
ordinary dependency. Select a published `fleet-model://` or `fleet-route://`
reference (or explicit quality-tier mapping). The Agent does not need
`models.platform_budget`, the virtual key, or an owner credential on this path.
Existing BYOK/budget/OAuth compatibility configuration remains available until
its data and selection migration is validated.

## Prepared Connector startup for the deployment bootstrap

The candidate builder packages the **same** Model Service Connector as v0.1.24
with a prepared startup entrypoint. It adds no runtime dependency or second
inference implementation:

```sh
python -m pantheon.models.connector_package \
  --output /absolute/new/model-connector --platform darwin-arm64
```

Stage this artifact through the ordinary Fleet lifecycle. Its backend requires
`values.connector`, accepting `engine`, `endpoint` and optional `secret_ref`.
The budget descriptor's `connector` object can be used directly. The generic
deployment entry has this shape (substitute the actual node and staged digest):

```json
{
  "node_id": "SELECTED_NODE",
  "revision": "STAGED_ARTIFACT_SHA256",
  "scope": "model-platform-budget",
  "generation": 0,
  "bindings": {},
  "components": {
    "backend": {
      "values": {
        "connector": {
          "engine": "api",
          "endpoint": "https://YOUR-PAIRED-HUB/litellm/v1",
          "secret_ref": "node-secret://platform-budget"
        }
      }
    }
  }
}
```

Fleet delivers its normal owner/node/instance/revision/generation-bound snapshot.
Startup initializes an empty connector, accepts an identical retained config
without rewriting it, and rejects conflicts. It does not resume engine recovery,
wake a model, rotate credentials or overwrite a service configured through RPC.
Use the existing explicit Model Services recovery/update flow for those actions.
Raw keys, owner tokens, legacy credential-file paths and managed-engine settings
are not accepted through this prepared value. Named credentials continue to use
the original node-local credential pipe. The existing interactive package remains
available for its current attach/managed-engine workflows.

This closes automatic **initial configuration** in the generic App deployment.
The owner bootstrap still needs to register the resulting exact binding in the
existing model directory, discover/publish the approved models, and compose its
consumer policy. Prepared startup alone does not perform these directory writes,
enable platform budget for a user, or switch a live Agent.

## Evidence and remaining work

`tests/test_model_platform_budget.py` uses the real Fleet vault and original
Connector. It exercises repeated provisioning without replacement, discovery,
streaming with unchanged direct/OpenRouter model IDs, credential conflicts,
wrong owners/nodes, malformed/oversized responses, redirect rejection and
cancellation. A native Agent process obtains a reply through the scoped model
dependency and this Connector, with no budget credential in its prepared config.
The Hub contract/auth tests live in `tests/test_platform_budget_connector.py` in
the Hub repository. Hub and inference responses are fixtures; these tests do not
prove live LiteLLM billing, deployed catalog completeness or production latency.

Still required: integrate credential delivery and model publication into the
owner bootstrap workflow; retain budget provenance in the model directory/UI;
map old selected models and enabled/disabled budget state without changing their
meaning; distribute paired releases; complete fenced migration and live
cross-node acceptance. This helper is not evidence that those steps are done.
