# Prepared Desktop backend candidate

This is the independent Desktop service used by the platform and by Agent tool
grants. It is not the Agent GUI or the native desktop distribution. Existing
entrypoints remain unchanged; complete General Team and product cutover are
still pending. The candidate currently targets macOS/Linux (POSIX file locks).

Build with `python -m pantheon.apps.builtin.desktop.build_managed --platform
darwin-arm64 --output /absolute/new/release`. Use `--data-mode tunnel` for a
Runner-assigned data port and a prepared public endpoint. The normal App install
hook installs the hash-locked requirements in the node-local dependency cache.
No Agent engine, settings singleton or CLI login implementation is shipped.

The owner supplies a generation-bound RuntimeConfiguration with `values.desktop`:

```json
{
  "user_seed": "owner-identity",
  "fleet": {"auth": "creds-base64"},
  "events": {"auth": "token"},
  "event_prefix": "selected.desktop",
  "catalog": [{"path": "/workspace/apps", "scope": "user"}],
  "data_roots": [],
  "store": {"origin": "https://store.example", "credential": "store"},
  "data": {"mode": "loopback"}
}
```

`fleet` and `events` are required credential aliases. Each pairs its exact NATS
endpoint with a token or base64 of the full user `.creds` contents, according to
`auth`. Base64 keeps the vault's bounded printable-key contract; it is not
encryption. Provision through the node credential vault, not manifest values.
TLS is required outside loopback. An explicit `ca_pem` may supply private TLS
trust. The two connections have independent ownership; they may use different
servers/accounts. Scope the event credential to the selected
`<event_prefix>.pantheon.stream.desktop` publication subject. Fleet credentials
belong to Desktop, never to the Agent consuming its ordinary tool grant.

Omit `store.credential` for explicit anonymous Store access. If present, the
`store` credential endpoint must match the origin. Optional `store.ca_pem`
supplies private trust. No ambient Store login or process proxy is consulted.

Workspace comes from the ordinary App host. Catalog paths and extra served roots
must be absolute, with exactly one `user` catalog. Workspace, catalog roots and
the user's normal Store snapshots/forks/repositories are served. State is kept
under `DATA/desktop-private`; overlapping served paths are rejected. Credential
files have private modes, remain available only while the connection owns them,
and are removed on close. Window documents survive close/reopen.

For remote delivery use `data: {"mode": "tunnel", "credential": "data"}`.
The `data` credential pairs the externally routed HTTPS origin with its path
token. The Runner supplies `PANTHEON_PORT_DATA`; the owner must route that port
to the provided origin. Loopback data uses a private ephemeral port and ignores
legacy `LIVE_VIEW_DATA_*` variables. The deployment owner still needs to expose
the event namespace and data route to its frontend; this package does not create
a Hub tunnel or silently log into another Fleet.

Missing/stale configuration or failed authentication refuses startup. Connection
loss fails the explicit operation; it cannot join an ambient Fleet or instantiate
another local Browser. The ordinary host drains admitted calls on shutdown.

Browser placement uses the normal Browser App. Some native-control/local-render
tool paths still require migration from the embedded engine before full parity
acceptance. This candidate is therefore not ready for the default platform
switch. It retains the public method surface; those remaining paths are not
claimed to work in the independent package yet.
