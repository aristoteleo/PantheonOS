# Managed Fleet Apps: service routing and Office rollout

Implementation status, 2026-09-16: code and isolated integration tests completed;
not configured or deployed on the user's existing Fleet. This is the protocol-1
managed-App path. Existing stdio AppHost apps are not silently migrated.

## Runtime contract

1. Store provides an immutable App revision. The browser/Agent requests its
   installation on a specific Fleet node. The node verifies the artifact.
2. `fleet.json` declares requirements and lifecycle hooks. Native Office uses
   `process` components and a verified native-package install hook; it needs no
   Docker. Container packages explicitly declare `container_engine`, which
   Fleet prepares on the selected node. It never borrows another node's engine.
3. Runner executes lifecycle hooks and components, persists operation receipts,
   verifies readiness and registers only declared loopback service ports.
4. Hub authenticates the user, derives their Fleet identity and issues a scoped
   App grant for node + instance + revision + generation + component + port.
   Controller checks this binding with the node before issuing a one-use ticket.
5. Atrium exchanges that ticket at the App origin for a Secure/HttpOnly,
   SameSite=None, Partitioned cookie. Cookie acceptance is probed explicitly.
   Hub login tokens are never sent to an App service.
6. Controller requests a connection using NATS; Runner connects outbound to
   Controller over WebSocket and streams the registered local endpoint. HTTP
   uploads/downloads and native WebSocket traffic are copied with backpressure.
   Large payloads do not pass through NATS messages.

Browser origin: `https://<hash>.<app-domain>`, where hash is the first 32 hex
characters of SHA256(`instance_id:component:port:generation`). Different
instances and generations have separate cookies/native-editor script origins.
This is service routing, not a publicly accessible node port.

Fleet opens new windows only for `ready` instances; blocked/recovered instances offer “Reconnect to
finish saving”. Existing windows can renew
their connection and continue saving while an instance drains or reports
`stop_blocked`; the App enforces document admission. Reconcile preserves the
generation of a confirmed live process so a restored editor can finish saving;
it does not replay uncertain hooks or label that process ready. Only a
successful stop advances the generation. An old window is never silently rebound
to a new instance/generation. Reopen from the ready Fleet instance after restart.

## Environment configuration

Use separate domains and secrets for staging and production. Values below are
examples, not domains that have already been created.

Fleet Controller:

```sh
FLEET_APP_DOMAIN=apps.staging.example.org
FLEET_APP_UI_ORIGINS=https://staging.example.org,http://localhost:5173
# Existing FLEET_CONTROLLER_SERVICE_TOKEN must be at least 24 characters.
# Keep the existing authenticated NATS authority and --hub-url configuration.
```

- Wildcard DNS `*.apps.staging.example.org` and a corresponding TLS certificate
  must route to this Controller. Preserve Host. Do not route App hosts to Hub.
- Use an App domain isolated from the Atrium/Hub origin. Keep authentication
  cookies host-only and do not share broad parent-domain cookies with Apps.
- Forward the Controller's `/apps/tunnel/` route with WebSocket upgrades. Nodes
  need outbound HTTPS access to their configured Controller origin.
- Disable request/response buffering at the reverse proxy; allow at least 512 MiB
  requests for Office and sufficiently long upload/WebSocket timeouts. Rate-limit
  public connection attempts at the edge. Controller bounds active streams and
  refuses unregistered local ports and stale instance bindings.
- Configure Hub's existing `fleet_controller_url` and
  `fleet_controller_token` to the matching Controller. Do not put service or Hub
  signing secrets in App packages, browser storage or deployment command output.
- Upgrade Runner: inventory must report `app-lifecycle=1` and `app-services=1`.
  UI intentionally refuses old nodes instead of silently falling back.

Native Office works in the gVisor Workspace model. Only Apps which need nested
containers may require the optional VM configuration for newly created Workspaces:

```sh
MODAL_WORKSPACE_VM=true
MODAL_WORKSPACE_VM_MEMORY_MB=8192
```

VM mode supplies the namespace capabilities required by the node-managed engine.
The current gVisor Workspace cannot gain those capabilities by installing Docker
binaries. This flag only changes newly created Workspaces; it does not terminate
or replace existing ones. Both gVisor and VM Fleet state live under the mounted
`/workspace/.pantheon/fleet-node`. Actual production-volume semantics for nested
Docker/Postgres still require a deployment acceptance check. VM memory is fixed
at the configured allocation, not the old elastic ceiling.

## Office artifact and rollout

Generate a native Office package without overwriting the source
App. See the Hub repository's `native/office/README.md` for prerequisites and the
optional container variant:

```sh
python scripts/package_office_app.py \
  --source /absolute/runtime/apps/office \
  --output /absolute/release/office \
  --version 0.4.0 \
  --runtime native \
  --platform linux-amd64 \
  --hub-origin https://hub.staging.example.org \
  --service-domain apps.staging.example.org \
  --ui-origin https://staging.example.org \
  --ui-origin http://localhost:5173
```

The generated App retains skills/actions and adds `execution.protocol=1` plus
`fleet.json`. Its native processes own the session API, patched ONLYOFFICE, persistent
working copies, private instance keys and engine storage. No local Mac Office
URL or Docker socket is built into the package.

After deploying matching Controller/Hub/Runner/UI versions, publish/install the
managed release in Store. Opening a new Office window selects Workspace,
prepares dependencies, starts the instance and remembers its binding in window
arguments. Simultaneous opens serialize setup. To choose another eligible node,
use Fleet → Manage Apps → install/start → Open. The Office footer shows the node.

Keep old Office sessions and local data until each working copy is saved or
migrated and verified. Old unbound sessions deliberately retain their old service;
there is no automatic data migration or automatic deletion of local containers.
A release is not fully migrated until cloud open/edit/save succeeds without using
the old local API or engine. The current source-file transfer still depends on
the browser; persistent server-side transfer/writeback jobs remain future work.

## Verification performed

- Go lifecycle and gateway tests with race detection: immutable artifacts,
  idempotent operations, stale generation/undeclared-port rejection, safe drain,
  retained data, origin/ticket/credential isolation, streamed HTTP and WebSocket.
- Actual modified Office container through NativeDriver: install, readiness,
  authenticated route, checkpoint, stop, restart, retained working data, uninstall
  with data retained (136.72 seconds). Only temporary test resources were touched.
- Chromium with the image's actual native `api.js`: partitioned-cookie connection,
  cross-origin editor iframe, concurrent native editors on different instance
  generations and rejection from an unrelated website. A local test transport
  substitutes for public DNS/TLS; production ingress is not covered by this test.
- UI tests: correct selected node/revision, concurrent opens, dependency failure,
  bound session restoration, legacy Office saving and progressive loading.
- Hub tests: scoped grants cannot access full-user endpoints, wrong Fleet and
  generation are rejected, callbacks stay instance-local, persistent restart and
  drain behavior, package exclusion of private local files, VM configuration.

The optional browser test uses an isolated profile and requires Playwright plus
`PANTHEON_TEST_OFFICE_API` pointing to the built native API, and
`PANTHEON_TEST_PLAYWRIGHT_MODULE` pointing to Playwright's module. It never controls
the user's open browser. The optional container lifecycle test uses `PANTHEON_TEST_OFFICE_PACKAGE`; it
never stops unrelated containers. The native counterpart uses
`PANTHEON_TEST_OFFICE_NATIVE_PACKAGE` on a disposable node matching its package
and needs no Docker. Native packaging also accepts `darwin-arm64`,
`darwin-amd64` and `windows-amd64`. These are separate platform artifacts;
deploy a matching release and an updated Fleet Runner before installation.
Apple Silicon lifecycle and editor save round trips have passed. Intel Mac and
Windows machine acceptance remain pending; cross-compilation is not runtime validation.
