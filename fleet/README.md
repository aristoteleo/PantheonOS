# Pantheon-Fleet (Go)

The Go implementation of **Pantheon-Fleet** — the subsystem that lets a
PantheonOS **Agent** operate a pool of compute **Nodes**: run code on any Node
and (soon) move data directly between Nodes, with no external VPN.

Design doc: [`../docs/pantheon-fleet.md`](../docs/pantheon-fleet.md).

## Status

**Phase 1a — working & end-to-end tested:** a Node joins over NATS, advertises
its capability into the Registry (JetStream KV) with a heartbeat, and serves the
Agent's Tasks (shell / python).

Next:
- **Phase 1b** — data plane: go-libp2p (QUIC + DCUtR hole-punching + Circuit
  Relay v2) for direct Node↔Node Transfers. (`internal/dataplane` is the stub.)
- **Controller** — `--key` join flow (key → fleet id + scoped NATS creds + relay
  list). For now, dev mode uses `--nats` + `--fleet` directly.

## Layout

```
cmd/fleet       Runner — the binary each Node runs (`fleet up`)
cmd/fleetctl    Operator/debug CLI (seed of the Agent's Fleet toolset)
internal/proto      wire types + NATS subject layout
internal/node       stable identity + capability/load detection
internal/registry   JetStream KV node records + heartbeat
internal/control    serves run_task / ping over the cmd subject
internal/exec       runs a Task via subprocess (bash / python3)
internal/dataplane  (Phase 1b) libp2p data plane — interface only for now
```

## Quickstart (dev)

```sh
# build
go build -o /tmp/fleet ./cmd/fleet
go build -o /tmp/fleetctl ./cmd/fleetctl

# a JetStream-enabled NATS for local testing
nats-server -js -p 4223 -sd /tmp/fleet-nats-js &

# join a Node to fleet "test" (dev: bypass the Controller)
/tmp/fleet up --nats nats://localhost:4223 --fleet test --name tnode --labels cpu

# see the Fleet, then run code on a Node
/tmp/fleetctl nodes --nats nats://localhost:4223 --fleet test
/tmp/fleetctl run   --nats nats://localhost:4223 --fleet test --node <id> --code 'uname -sm'
/tmp/fleetctl run   --nats nats://localhost:4223 --fleet test --node <id> --kind python --code 'print(6*7)'
```

## Build a distributable binary

Pure Go (no CGO) → fully static, trivially cross-compiled:

```sh
CGO_ENABLED=0 GOOS=linux  GOARCH=amd64 go build -o dist/fleet-linux-amd64  ./cmd/fleet
CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -o dist/fleet-darwin-arm64 ./cmd/fleet
```
