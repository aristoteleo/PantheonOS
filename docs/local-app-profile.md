# Local App profiles

`pantheon local` is an opt-in macOS/Linux host for an explicit composition of
ordinary Apps. It starts a private Controller, NATS broker and Runner, then uses
the ordinary Fleet deployment and Model Services coordinators. It does not join
the installed Fleet, use ambient Fleet credentials, open a REPL, or open a Desktop
window. Existing `pantheon cli` and `pantheon ui` defaults are unchanged.

This is the local lifecycle building block for those product launchers. A product
bundle must currently supply the three executables and already-built App packages;
there is no automatic download, default Agent recipe or native Desktop installer.
The host itself imports no Agent implementation.

## Start

Create an existing workspace directory, a private manifest file (`chmod 600`),
and choose a new profile directory. Run from the checkout/environment containing
this implementation:

```sh
pantheon local \
  --profile /absolute/path/to/profile \
  --workspace /absolute/path/to/workspace \
  --manifest /absolute/path/to/profile.json \
  --controller /absolute/path/to/controller \
  --broker /absolute/path/to/nats-server \
  --runner /absolute/path/to/fleet
```

The host holds the profile's exclusive local lock. Its directory contains durable
Fleet identities, private TLS material, credentials, process logs, installed App
artifacts, App-owned data, and the composition journal. Keep this directory across
clean exits. Do not share one profile between concurrently running hosts.

Stdout contains JSON status objects. `ready` means the selected composition has
passed its ordinary startup checks; it is not evidence of a connected GUI.
`needs_attention` means the current operation needs review while healthy Fleet
infrastructure is retained. Public statuses do not contain manifest configuration,
model publications, bearer tokens or arbitrary transport exception text.

- **Ctrl-C or SIGTERM:** request ordered App shutdown. Wait for `stopped` and the
  process to exit. App data and installed artifacts remain available.
- **SIGUSR1:** after addressing an error, retry the same startup or stop operation.
  This does not invent a replacement operation or launch a duplicate instance.
- **Embedded callers:** `serve(..., commands=queue, on_status=callback)` accepts
  `retry`, `stop` and `status` commands. It does not change the caller's signal
  handlers. Its lifetime owns Fleet; do not cancel it as a substitute for draining
  Apps. A callback should remain available for the entire host lifetime.

A stop request during startup is processed after that advancement returns. An
incomplete startup cannot currently be rolled back automatically: it must be
resumed to completion before an ordered stop. Abrupt host/process failure and a
new authority endpoint require explicit recovery; do not delete journals or edit
recorded generations to force another startup.

## Manifest

The version-1 manifest has exactly `protocol`, `packages`, `apps`, and
`model_apps`. Example shape for an already-built App without configuration:

```json
{
  "protocol": 1,
  "packages": {
    "example": {
      "path": "/absolute/path/to/built-app",
      "revision": "REPLACE_WITH_64_CHARACTER_ARTIFACT_SHA256"
    }
  },
  "apps": {
    "example": {
      "package": "example",
      "scope": "local-example",
      "components": {},
      "bindings": {}
    }
  },
  "model_apps": {}
}
```

This shape is not a runnable sample until the path and revision refer to a real
package. Calculate the digest using the same artifact builder used for staging:

```python
from pathlib import Path
from pantheon.apps.lifecycle import build_artifact
_, revision = build_artifact(Path('/absolute/path/to/built-app'))
print(revision)
```

Each App's immutable manifest determines its required components, configuration,
credentials and dependency interfaces. Supply these through ordinary `components`
and `bindings`; the host validates them before starting the App. `$app` and
`$model` references use the existing deployment/bootstrap contract. There may be
1–24 used packages, 1–16 consumer Apps and up to 8 attached model Apps. Input JSON
is limited to 64 KiB and must not contain duplicate keys.

Inside component configuration and bindings, an exact object `{"$local": NAME}`
resolves one of these profile-owned values:

| Name | Value |
| --- | --- |
| `controller` | Current loopback HTTPS authority URL |
| `trust_roots_pem` | This profile's CA certificate |
| `directory_root` | Its private Model Services directory path |
| `workspace` | Its explicit workspace path |
| `owner_credential` | Endpoint-scoped node-vault reference, never an inline key |

Use `owner_credential` only for owner brokers whose declared configuration needs
it. Consumer Apps receive scoped dependency bindings. Do not put plaintext API
keys in manifests; the host provisions only its own endpoint-scoped owner vault
entry, not arbitrary external-provider credentials.

An attached-model provider has the following shape in `model_apps`:

```json
{
  "connector": {
    "deployment_id": "local",
    "name": "Local model",
    "models": [{"id": "example:8b", "context_limit": 4096}],
    "app": {
      "package": "connector",
      "scope": "model-local",
      "components": {
        "backend": {
          "values": {
            "connector": {
              "engine": "ollama",
              "endpoint": "http://127.0.0.1:11434"
            }
          }
        }
      },
      "bindings": {}
    }
  }
}
```

The Connector package must be declared in `packages`, and the endpoint/model must
actually exist. The original Model Services manager verifies the publication.
This host does not start an Ollama/LM Studio/SGLang engine for that endpoint; the
current profile format supports attached providers. The model-access broker and
Agent or other consumers use the original Model Services binding mechanisms.
For complete tested compositions, see `tests/test_local_profile.py` and
`tests/test_local_profile_agent.py`.

## Lifetime and recovery boundaries

Installed immutable packages are reused on subsequent launches; their source
bundles are not rebuilt/uploaded and install hooks are not rerun. A missing
installation requires the original package and matching digest. Changing the
manifest, workspace, owner, or CA is an explicit reconfiguration/migration task,
not an implicit restart. There is not yet a profile-upgrade command.

After an acknowledged clean shutdown, the next launch verifies the stopped App
generations, prepares new generations, rebinds the original stopped model
publications, and starts consumers against the fresh local endpoint. Model
selection and App data remain unchanged. During shutdown, consumers drain before
providers; unconfirmed or failed drains keep infrastructure available. Other
running Apps on the profile prevent infrastructure shutdown.

The host periodically maintains the original dependency grants. Failed
observations are reported and retried; they never replay App starts or tool calls,
issue replacement grants, or revive expired authorization. Changed maintenance
warnings and recovery are reported through `dependency_maintenance` statuses.

Native tests cover clean reopen, model calls through the original Connector,
retained Agent conversations and distinct real Shell sessions after restart,
installed-artifact reuse, SIGINT shutdown, and resuming a lost startup reply.
Upstream model replies in those tests are fixtures. Abrupt crash recovery,
managed-engine recovery, Windows hosting, cross-node orchestration, automatic
CLI/Desktop composition and full release migration remain separate work.
