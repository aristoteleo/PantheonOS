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
