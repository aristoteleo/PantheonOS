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

**Still open:** kill the static `pbk_` key in favour of OAuth-session-derived
creds (needs a prod-hub deploy + coordination with the shared-env owner).
