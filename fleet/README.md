# Pantheon-Fleet (Go)

The implementation of **Pantheon-Fleet** — the subsystem that lets a PantheonOS
**Agent** operate a pool of compute **Nodes**: run code on any Node and move
data directly between Nodes, with no external VPN.

Design doc: [`../docs/pantheon-fleet.md`](../docs/pantheon-fleet.md).

## Status — Phase 1 complete & end-to-end verified

| Piece | What | Verified |
|---|---|---|
| **Runner** (`cmd/fleet`) | join, capability/heartbeat into the Registry, serve Tasks | ✅ |
| **Control plane** (NATS + JetStream KV) | registration, commands, signaling, progress | ✅ |
| **Execution** | shell / python Tasks via subprocess | ✅ |
| **Data plane** (`internal/dataplane`, libp2p) | direct Node↔Node Transfers (QUIC), chunked + sha256 | ✅ 50 MB @ ~200 MB/s, sha match |
| **Controller** (`cmd/fleet-controller`) | `--key` → Fleet (one per user) | ✅ |
| **Relay** (`cmd/fleet-relay`) | Circuit Relay v2 node (data-plane fallback) | ✅ boots; node accepts it |
| **Installer** (`scripts/install.sh`) | `curl … | sh` bootstrap | ✅ |
| **Agent toolset core** (`python/fleet_client.py`) | list_nodes / run / transfer / ping | ✅ Python↔Go interop |

Needs real infra to exercise (not locally self-verifiable): the relay-*fallback*
data path (needs a NAT'd environment), key validation against the PantheonOS
hub, scoped-NATS-JWT isolation, and hosted binary distribution.

## Layout

```
cmd/fleet            Runner — the binary each Node runs (`fleet up`)
cmd/fleetctl         operator/debug CLI: nodes / run / ping / transfer
cmd/fleet-controller key → Fleet join service
cmd/fleet-relay      libp2p Circuit Relay v2 node
internal/proto       wire types + NATS subject layout + Controller join types
internal/node        stable identity + capability/load detection
internal/registry    JetStream KV node records + heartbeat + peer lookup
internal/runner      serves run_task / transfer / ping; drives the data plane
internal/exec        runs a Task via subprocess (bash / python3)
internal/dataplane   libp2p host + sha256-verified transfer stream
internal/join        Runner's Controller client
python/fleet_client.py  async client — seed of the Agent's Fleet toolset
scripts/install.sh   curl bootstrap
```

## Quickstart (dev)

```sh
go build -o /tmp/fleet            ./cmd/fleet
go build -o /tmp/fleetctl         ./cmd/fleetctl
go build -o /tmp/fleet-controller ./cmd/fleet-controller
go build -o /tmp/fleet-relay      ./cmd/fleet-relay

nats-server -js -p 4223 -sd /tmp/fleet-nats-js &        # JetStream NATS

# (optional) the Controller — join by key instead of --nats/--fleet
/tmp/fleet-controller --nats nats://localhost:4223 --addr :8099 &

# join two Nodes (dev: bypass the Controller); --state-dir lets several share a host
/tmp/fleet up --nats nats://localhost:4223 --fleet test --name a --state-dir /tmp/a &
/tmp/fleet up --nats nats://localhost:4223 --fleet test --name b --state-dir /tmp/b &

/tmp/fleetctl nodes    --nats nats://localhost:4223 --fleet test
/tmp/fleetctl run      --nats nats://localhost:4223 --fleet test --node <A> --code 'uname -sm'
/tmp/fleetctl transfer --nats nats://localhost:4223 --fleet test --src <A> --dst <B> \
                       --src-path /path/big.bin --dst-path /tmp/out.bin
```

## Build distributable binaries

Pure Go (no CGO) → fully static, trivially cross-compiled:

```sh
CGO_ENABLED=0 GOOS=linux  GOARCH=amd64 go build -o dist/fleet-linux-amd64  ./cmd/fleet
CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -o dist/fleet-darwin-arm64 ./cmd/fleet
```

> **`go mod tidy` note:** go-libp2p's nested `core` module exposes a test-only
> `network/mocks` package that makes `go mod tidy` report an ambiguous import.
> It does not affect the build (mocks are test-only). Use `go build ./...` (and
> `go vet ./internal/... ./cmd/...`); if you must tidy, use `go mod tidy -e`.
