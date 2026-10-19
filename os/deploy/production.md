# Deploying a secured Fleet (Controller + NATS + relay) on one host

This stands up the whole control plane on a single public droplet: the
**Controller** (decentralized-JWT Authority + access gate), **NATS** (JWT auth +
JetStream), and a **relay** — plus optionally an on-host node. A laptop behind
NAT then joins with `fleet up --controller http://<host>:8099 --key <key>`.

A reference deployment runs on DigitalOcean (SFO3, `s-1vcpu-1gb`, ~$6/mo).

## 0. Build (pure Go, static)

```sh
for c in fleet-controller fleet-relay fleet fleetctl; do
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o $c ./cmd/$c
done
scp fleet-controller fleet-relay fleet fleetctl host:/root/fleet/
# nats-server: grab the matching release on the host
curl -fsSL https://github.com/nats-io/nats-server/releases/download/v2.12.4/nats-server-v2.12.4-linux-amd64.tar.gz | tar xz
```

## 1. Access gate

**Preferred — hub validation.** Point the Controller at the PantheonOS hub; it
validates each `pbk_` key via the hub's platform-key API and maps it to the
user's fleet. Users mint keys with `POST /api/platform-keys/create` (logged in);
revoke with `DELETE /api/platform-keys/{id}`.

```sh
fleet-controller --hub-url https://pantheon.aristoteleo.com \
                 --hub-token $FLEET_CONTROLLER_SERVICE_TOKEN  …
```

The hub must run with the same `FLEET_CONTROLLER_SERVICE_TOKEN` (it protects the
`/api/platform-keys/validate` endpoint). One fleet per **user** — all of a
user's keys map to the same fleet.

**Interim — static allowlist** (no hub dependency): only listed keys may join;
each distinct key → its own fleet. One key per line in a 0600 file:

```sh
echo "pbk_$(openssl rand -hex 20)" > /root/fleet/allowed-keys && chmod 600 /root/fleet/allowed-keys
fleet-controller --allowed-keys-file /root/fleet/allowed-keys  …
```

## 2. Bring it up (order matters)

```sh
IP=<public-ip>
# relay first — its peer id is stable via --identity
fleet-relay --port 4250 --announce $IP --identity /root/fleet/relay.key --limit-mb 5120 --limit-min 120 &
RELAY=/ip4/$IP/udp/4250/quic-v1/p2p/<peer-id-from-relay-log>

# controller: bootstrap auth, emit the nats config, gate, advertise the relay
fleet-controller --auth --addr :8099 \
  --nats nats://$IP:4222 --nats-listen 0.0.0.0:4222 \
  --js-store-dir /root/fleet/js --state-dir /root/fleet/ctl \
  --allowed-keys-file /root/fleet/allowed-keys \
  --relays $RELAY --emit-nats-config /root/fleet/nats.conf &

# nats with the emitted JWT config
nats-server -c /root/fleet/nats.conf &
```

**Use the relay's PUBLIC address** in `--relays` (the relay logs its private DO
address too — don't pick that one) or NAT'd peers can't reach it for fallback.

## 3. Firewall

```sh
ufw allow 22/tcp && ufw allow 8099/tcp && ufw allow 4222/tcp && ufw allow 4250/udp && ufw --force enable
```

NATS on 4222 is public but **JWT-protected** (anonymous → Authorization
Violation). The Controller on 8099 is **allowlist-gated**. The relay is open but
bounded (`--limit-mb/--limit-min`).

## 4. Make it reboot-durable (systemd)

One unit each for `fleet-relay`, `fleet-controller`, `fleet-nats`
(`After=fleet-controller`, `ExecStartPre` waits for `nats.conf`), and an optional
`fleet-node`. `Restart=always`. See the unit templates this repo's deploy script
writes; enable with `systemctl enable --now fleet-{relay,controller,nats,node}`.

## 5. Join from anywhere

```sh
fleet up --controller http://<host>:8099 --key <allowed-key> --name laptop
```

The node validates against the gate, receives scoped `.creds` + the relay list,
connects to NATS with a per-fleet inbox, and registers. Drive it with the
`FleetToolSet` (`controller_url=` + `key=`) or watch it with `fleet-dashboard
--creds`.

## Notes

- **Isolation:** a credential is scoped to `fleet.<fid>.>`, the KV bucket
  `FLEET_<fid>_NODES`, and `_INBOX_<fid>.>` — a key for fleet A cannot see or
  touch fleet B. Verified by `internal/auth` TestAuthorityIsolation.
- **Keys persist** in `--state-dir` (operator/account seeds); the account public
  key is stable across restarts, so issued creds keep working.
- **Not yet hardened for untrusted multi-tenant use:** keys are gated, not
  hub-authenticated; treat as a private/reference deployment until hub platform
  keys land.

## Security hardening (2026-07-05)

Built on the P0/P1 credential model (short-lived creds + proof-of-possession
refresh + single-use join tokens + revocation — see
`docs/fleet-security-model.md`). All on `feat/pantheon-fleet`, all live on the
prod controller behind `https://fleet.aristoteleo.com` (droplet `581354476`);
controller binaries backed up on the host as `fleet-controller.bak-p3{,b,c,d}-*`.

**Revocation now kicks — and stays kicked**

- `12749f8` — revoke KICKS the node off NATS immediately: the Controller adds the
  node's current user credential to the FLEET account JWT's revocation list,
  re-emits the nats config, and `pkill -HUP nats-server`. Server-enforced, so a
  compromised node can't ignore it. (`node_id → user pubkey` is recorded at every
  `/join` and `/token`.)
- `f88ef0c` — `/join` now also enforces `revoked.json` (previously only `/token`
  did) — otherwise a kicked node still holding the API key just re-joins for a
  fresh credential. Both endpoints now refuse a revoked node key.
- `abbad8f` — a fresh single-use join token (only the owner can mint one)
  REINSTATES a revoked node: `/join` clears it from `revoked.json` and lets it
  back in. A bare `--key` re-join of a revoked node stays refused. This is how
  you bring back a node you revoked.
- `eafa227` — revoke deletes the node's registry KV record at once, so it leaves
  the Cluster panel immediately instead of lingering ~30s until its heartbeat TTL
  lapses (which read as "the kick didn't work").
- `3ddcdac` — a revoked node's `fleet up` detects the revocation (a `/token`
  "node revoked"), prints a plain-language notice, and exits on its own instead of
  reconnecting forever. Client change → all four `fleet-v0.1.0-alpha` release
  binaries rebuilt + re-uploaded (else `install.sh` serves the old one).

**Transport is TLS-only**

- Controller behind Caddy + Let's Encrypt; NATS TLS via the `nats-tls.conf`
  wrapper (`include nats.conf` + a `tls {}` block).
- `allow_non_tls` removed and external `:8099` firewalled: plaintext NATS is
  rejected (`tls_required: true`) and the plaintext HTTP controller port is closed
  — Caddy's localhost proxy keeps `:443` serving. GOTCHAs: dropping `allow_non_tls`
  needs a full `systemctl restart fleet-nats` (a SIGHUP reload does NOT apply it);
  and check every client's actual transport (`journalctl -u <svc> | grep -oE
  'tls://|nats://'`), including the droplet's OWN `fleet-node`, before flipping it —
  a node that joined pre-TLS silently stays plaintext until it re-joins.

**Least-privilege scoping**

- P1 mints a NARROW per-node credential (`MintFleetNode`) that can only serve its
  own `node.<id>.cmd` + progress subjects and write its own registry key — a hacked
  node can't command or eavesdrop on its peers. The agent (no node id) keeps the
  broad credential it needs to command every node.
- `c0ab123` — `node_id → node_pub` is bound on first claim (TOFU): since a node_id
  is scoped into the per-node credential, `/join` rejects (409) a second key trying
  to claim an existing node_id, closing a narrow impersonation/eavesdrop path for a
  join-token holder who knows a peer's id.

## D-full: killing the static `pbk_` key (session-derived creds)

The local backend can derive its `FLEET_KEY` from the user's platform session
instead of holding a static `pbk_` bearer key — same model the hub already uses
for sandboxes (`build_agent_env_vars`). The mechanism is BUILT and validated
end-to-end on a local rig; the prod switch is a coordinated migration because it
moves the fleet id from **key-derived** (`deriveFleet(key)`, today's
`--allowed-keys` gate) to **user-derived** (`_fleet_id_for_user`, hub-validated).

Pieces (all committed):

- **hub** `POST /api/fleet/credential` — mints a short-lived, fleet-scoped JWT for
  the authenticated caller (commit `71ceb58`, pantheon-hub; NOT yet pushed/deployed).
- **controller** `resolveFleet()` — `/join`, `/join-tokens`, `/revoke` accept
  EITHER a hub-validated credential (session JWT, or a pbk_ the hub knows) OR a
  local allowlisted key; hub first, allowlist fallback (commit `8728bbf`). Inert
  with no `--hub-url` — safe to ship ahead of the switch.
- **backend** `pantheon/chatroom/fleet_session.py` + wiring — when logged in and no
  static `pbk_` `FLEET_KEY` is set (or `FLEET_PREFER_SESSION_CRED=1`), each fleet
  tool exchanges the session token for a fleet key via the hub and refreshes it
  (commit `edbff51`). Back-compat: a static `pbk_` key is left untouched.

Validated locally: `create_access_token(scope=fleet)` → the hub's `/validate`
returns the user's fleet id; a mock hub + a `--hub-url` controller resolve a session
JWT to the user-derived fleet + mint narrow creds; a static allowlisted key still
resolves to its key-derived fleet; unknown keys → 403; the backend helper fetches
and publishes `FLEET_KEY` (and keeps a `pbk_` key untouched).

Prod rollout (coordinate the shared prod hub with its owner — order matters):

1. **Prod hub** — push `71ceb58` to hub master and deploy the hub so
   `POST /api/fleet/credential` + the fleet-JWT path of `/api/platform-keys/validate`
   are live (this arms the already-merged fleet integration too).
2. **Register the key user-side** — create the user's `pbk_` key in the prod hub
   (so `/validate` maps it to `user_id`), OR accept that the static key keeps its
   key-derived fleet during migration (split-brain — avoid by doing this step).
3. **Controller** — deploy `8728bbf` and add
   `--hub-url https://pantheon.aristoteleo.com` (+ the existing service token). With
   the allowlist still present, static keys keep working (fallback); session JWTs
   now resolve to the user-derived fleet. Reversible: drop `--hub-url` to revert.
4. **Backend** — unset the static `FLEET_KEY` (ensure the backend is logged in as
   the real user, not a seed account) → it fetches a session cred → user-derived
   fleet.
5. **Re-join nodes** — the Mac (and any node) must re-join to register in the
   user-derived fleet; the old key-derived registration is a different fleet.

Caveat found during design: the local backend's `store_auth.json` may be a seed
login (`store-seed`) whose `user_id` ≠ the real user, which would mint a JWT for
the wrong fleet — confirm the backend's session identity before step 4.

### Prod cutover checklist — state VERIFIED 2026-07-06 (HELD, pending BAKEZQ + a window)

Read-only assessment of prod on 2026-07-06 (this is a full hub version jump, NOT a
config flip like staging):

- **prod hub** (`pantheon.aristoteleo.com`) runs an OLD image `sha256:a9e8c474…`;
  the whole fleet + platform-keys API is absent — `/api/fleet/credential`,
  `/api/fleet/nodes`, `/api/platform-keys/validate` all return **404**. So step 1
  is a real image jump to current master.
- **hub env delta prod↔staging = EXACTLY 2 vars**: staging has `FLEET_CONTROLLER_URL`
  + `FLEET_CONTROLLER_SERVICE_TOKEN`; prod has neither. All other ~48 vars (incl.
  `PANTHEON_SECRET_KEY`, the JWT signing key `/credential` needs) are identical →
  the jump's fleet config surface is just these two, inert until the endpoints are hit.
- **prod controller** (`fleet.aristoteleo.com` = 24.199.99.134, droplet `581354476`):
  `--allowed-keys-file` only; **no `--hub-url`, no `--hub-token`**; NATS already TLS.
  11 node registrations persisted (nodepubs.json, incl. offline — not all live).
- **hub DB**: `init_db()` runs `create_all` on boot = additive (creates missing
  tables, never alters existing). Column changes live in hand-applied
  `migrations/*.sql`, NOT auto-run — so the jump is largely reversible except any
  SQL you apply in step 0.

**Target image:** pin the EXACT digest staging has already validated
(`sha256:66c270be…` as of 2026-07-06) via `kubectl set image` — do NOT bare-restart
on the shared `:latest` tag (prod+staging share `pantheon-hub:latest` at DIFFERENT
digests; a naive restart could pull an unintended build). Re-confirm the digest at
cutover.

Order (each step reversible unless noted):

0. **Pre-req — DB schema (BAKEZQ / DB owner):** confirm prod DB has what the target
   image expects. `create_all` makes brand-new tables (e.g. platform_keys) on boot,
   but column-adds to existing tables need the pending `migrations/*.sql` since prod's
   last deploy (`add_data_shares`, `add_per_app_chatrooms`, `add_user_preferred_region`,
   …). A missing column = runtime 500s. This is the main not-cleanly-reversible gate
   (applied SQL stays) — review first. Quick check: `SELECT to_regclass('platform_keys');`
1. **prod hub env** — add `FLEET_CONTROLLER_URL=https://fleet.aristoteleo.com` and
   `FLEET_CONTROLLER_SERVICE_TOKEN=<new prod SVCTOKEN>`. Rollback: remove them.
2. **prod hub image** — `kubectl --context do-sfo3-pantheon-sfo -n pantheon set image
   deploy/pantheon-hub pantheon-hub=…/pantheon-hub@sha256:66c270be…`. Rollback:
   `kubectl rollout undo deploy/pantheon-hub` (prior digest `a9e8c474`). Verify:
   `/api/fleet/nodes` no longer 404.
3. **prod controller** — drop-in `--hub-url https://pantheon.aristoteleo.com
   --hub-token <same prod SVCTOKEN>` (KEEP `--allowed-keys-file` as fallback), then
   `systemctl daemon-reload && systemctl restart fleet-controller`. Reversible: delete
   the drop-in + restart. Drop-in body prepared at `deploy/dropins/prod-hub-url.conf`.
4. **backend/session** — the web sandbox already mints session creds; once the
   controller hub-validates, session JWTs resolve to the user-derived fleet.
5. **re-join nodes** — the Mac + any keeper node must re-join (fresh join-token from
   the now-live panel) to land in the user-derived fleet; old key-derived
   registrations are a different fleet id.

**SVCTOKEN:** generate a FRESH random secret at cutover, set identically on hub
`FLEET_CONTROLLER_SERVICE_TOKEN` and controller `--hub-token`. Never reuse staging's.
