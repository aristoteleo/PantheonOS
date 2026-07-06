# Pantheon-Fleet — Design & Implementation Plan

> Status: **Phase 1 done (incl. NATS auth); Phase 2/3 in progress; reference stack live on DO** · Owner: Nanguage · Last updated: 2026-06-30
>
> The Go implementation lives in [`../fleet/`](../fleet/); the Agent toolset in
> [`../pantheon/toolsets/fleet/`](../pantheon/toolsets/fleet/). Done & verified:
> Runner, control plane, data plane (direct + **relay-fallback**), Controller
> (key join), relay, installer, and the **Fleet agent toolset** (observe / run /
> run_on_label / transfer / broadcast / gather / pick_node). Transfers support
> **zstd compression** and **resume**. The one open Phase 1 item — scoped NATS
> JWT + signed Node identity — needs the isolation-model decision (§13).
> See §12 for per-phase status and the fleet README for what needs real infra.

Pantheon-Fleet lets a PantheonOS **Agent** operate a pool of computers it can
see, command, and move data between. A user runs a single binary on any
machine (laptop, cloud VM, HPC login node) and that machine joins the user's
**Fleet**; the Agent can then run code on any node and schedule high-speed,
direct node-to-node data transfers — through NAT, with no external VPN.

---

## Table of contents

1. [Summary](#1-summary)
2. [Goals / Non-goals](#2-goals--non-goals)
3. [Naming & glossary](#3-naming--glossary)
4. [Architecture](#4-architecture)
5. [Components](#5-components)
6. [Wire protocol & data model](#6-wire-protocol--data-model)
7. [Key flows](#7-key-flows)
8. [Agent interface (Fleet toolset)](#8-agent-interface-fleet-toolset)
9. [Security model](#9-security-model)
10. [Special environments (HPC, NAT, cloud)](#10-special-environments)
11. [Tech stack & language choice](#11-tech-stack--language-choice)
12. [Phased rollout](#12-phased-rollout)
13. [Open questions / decisions](#13-open-questions--decisions)
14. [Appendix](#14-appendix)

---

## 1. Summary

The central design decision is **separating the control plane from the data
plane**:

- **Control plane = NATS** (already in PantheonOS): registration, heartbeats,
  inventory, commands, NAT-traversal signaling, progress. Small messages —
  exactly what NATS is good at.
- **Data plane = libp2p/QUIC**: direct node-to-node bulk transfer with NAT
  traversal and relay fallback. NATS is **not** used for bulk data (it jams on
  messages ≳128 KB / under concurrency at the ~6 MB ingress ceiling — a limit
  PantheonOS has already hit). This is the same split WebRTC and Tailscale use.

A user's machines form a **Fleet** (one per user, scoped by API key). Each
machine runs a **Runner** (a single distributable Go binary). The **Agent**
sees every **Node** via a live **Registry** and drives the Fleet through a
dedicated toolset.

```
   curl … | sh -s -- fleet up --key pbk_xxx
                 │
                 ▼
            ┌──────────┐   join (key → Fleet, scoped creds)   ┌────────────┐
   Node ───►│  Runner  │◄─────────── Control plane (NATS) ───►│ Controller │
            └────┬─────┘                                       └────────────┘
                 │  Data plane (libp2p/QUIC, direct; Relay fallback)
                 ▼
            other Nodes
```

---

## 2. Goals / Non-goals

### Goals
- **Zero-install onboarding**: one `curl` + one command turns any machine into a
  Node. Single self-contained binary, all major OS/arch.
- **Agent visibility**: the Agent can clearly see every Node's capability and
  live state.
- **Agent control**: run code on a named Node (or by label), and schedule
  node-to-node data transfers.
- **High-speed, stable transfers**: saturate the link; survive NAT; resume on
  failure; verify integrity.
- **No external VPN dependency**: NAT traversal + encryption + relay are
  embedded in the binary.
- **Per-user isolation**: Fleets are separated by API key.

### Non-goals (for now)
- A general distributed-compute / dataflow scheduler. The Agent is the
  orchestrator; Fleet provides primitives (run, transfer), not auto-placement.
- Multi-tenant shared node pools across users (each Fleet is single-user).
- Replacing HPC schedulers (Slurm) or Globus — we integrate with them where it
  helps, not reimplement them.

---

## 3. Naming & glossary

**Naming discipline:** the word **"Agent" is reserved for the AI** (the
LLM-driven actor). The infrastructure layer **never** reuses "agent" — this is
what removes the Agent-vs-node-program ambiguity. Only the *system* gets a brand
name (**Fleet**); everything else uses plain, industry-standard terms.

| Term | Definition | Clarification / what it is **not** |
|---|---|---|
| **Agent** | The AI actor (LLM-driven). Decides and commands. | Reserved word; the infra layer never reuses it. |
| **Fleet** | The subsystem; also a user's pool of Nodes (one per user, key-scoped). | Replaces the generic word "cluster". |
| **Node** | One machine in a Fleet. | A machine that is running a Runner. |
| **Runner** | The single self-contained binary on each Node. Handles join, reporting, code execution, and data transfer. | **Not** an "agent" — it only executes; it has no intelligence. CLI/daemon name: `fleet`. |
| **Task** | One code/command execution on a Node, issued by the Agent. | Use this consistently; do not mix "job/run". |
| **Transfer** | One bulk data move between two Nodes (or Node↔storage), scheduled by the Agent. | — |
| **Control plane** | The NATS layer: registration, heartbeats, inventory, commands, NAT signaling, progress. | Small messages only — no bulk data. |
| **Data plane** | The libp2p/QUIC layer: direct Node↔Node bulk transfer + NAT traversal + relay. | Never over NATS. |
| **Registry** | The live inventory of Nodes (NATS JetStream KV, TTL'd). | The Agent's "view" of the Fleet. |
| **Controller** | Server-side service: validate key → assign Fleet → issue scoped credentials → maintain Registry. | The gatekeeper; it does not execute Tasks. |
| **Relay** | Self-hosted public-IP node(s) that bridge data-plane connections when hole-punching fails. | Fallback path only. |

**One-line narrative (put it on the wall):**
> The **Agent** commands its **Fleet**. Each **Node** runs a **Runner**. Runners
> execute **Tasks** and move data via **Transfers**. Control flows over the
> **Control plane (NATS)**; bulk data flows Node-to-Node over the **Data plane
> (libp2p)**. The **Controller** admits Nodes by key; the **Relay** is the
> fallback path.

---

## 4. Architecture

### 4.1 Control plane / data plane split (the core principle)

| | Control plane (have: NATS) | Data plane (new) |
|---|---|---|
| Carries | registration, heartbeat, inventory, commands, **NAT signaling**, progress | **bulk data** between Nodes |
| Message size | KB | GB–TB |
| Path | Agent ↔ NATS ↔ Node | **Node ↔ Node direct** (not via NATS, not via the Agent) |
| Tech | NATS req/reply + JetStream KV/JWT | libp2p (QUIC + DCUtR hole-punch + Circuit Relay v2) |

**Elegant reuse:** NATS doubles as the *signaling channel* for the data plane.
Every Node is already on NATS, so peers exchange their libp2p addresses
(multiaddrs) via the Registry/NATS, then establish a direct QUIC connection
themselves. NATS does small signaling; libp2p does bulk data.

### 4.2 Why this split

NATS is a messaging bus, not a bulk-data transport. PantheonOS already
discovered NATS jams on messages ≳128 KB or under any concurrency (~6 MB
LB/ingress ceiling). Forcing data through it is the wrong tool. A separate
direct-connection data plane is how every comparable system (WebRTC, Tailscale,
IPFS) is built.

---

## 5. Components

### 5.1 Runner (the per-Node binary)

A single static Go binary. Modules:

- **control** — NATS connection (with the Fleet-scoped JWT), request/reply for
  commands, event publishing.
- **registry** — periodically writes this Node's record (capability + live
  state + multiaddrs) to the JetStream KV bucket with a TTL (heartbeat).
- **exec / supervisor** — runs Tasks by spawning subprocesses (`bash`, `python3`),
  streaming stdout/stderr, enforcing timeouts and limits. (v2: supervise the
  full Python toolset endpoint as a managed child for richer tools.)
- **dataplane** — embeds go-libp2p: QUIC transport, DCUtR hole-punching,
  Circuit Relay v2 client, Noise/TLS. Opens/accepts transfer streams to other
  Nodes; chunks, hashes, resumes.
- **cli** — `fleet up --key …`, `fleet status`, `fleet down`.

The Runner is **dumb and loyal**: it executes what the authenticated control
plane tells it. All intelligence lives in the Agent.

### 5.2 Controller (server-side)

- **Join/auth**: validate the user's API key (`pbk_…`) → resolve `fleet_id`
  (= user id) → mint a **scoped NATS user JWT** (permissions limited to
  `fleet.<fleet_id>.>`) → issue a signed Node identity (libp2p key material /
  membership cert) → return the Relay multiaddrs.
- **Registry custodian**: owns the JetStream KV bucket lifecycle and TTLs;
  optionally surfaces an aggregated Fleet view to the UI.
- **Revocation**: invalidate a Node / rotate Fleet membership.

The Controller is a small stateless-ish service in front of NATS + an auth
backend; it can live alongside the existing hub.

### 5.3 Control plane — NATS

- **JetStream KV** for the Registry (live Node records, TTL = liveness).
- **NATS accounts / decentralized JWT** for per-Fleet isolation — a Node's JWT
  only grants `fleet.<fleet_id>.>`, so Fleets cannot see or touch each other.
- **Request/reply** for commands (run Task), **pub/sub** for events, signaling,
  and progress.

### 5.4 Data plane — libp2p (codename: *Talaria*, optional)

Use **go-libp2p** rather than hand-rolling NAT traversal:

- **QUIC** transport (encrypted, multiplexed, resumable; UDP → NAT-friendly).
- **DCUtR** — hole-punching coordinated through a relay.
- **Circuit Relay v2** — relay fallback when a direct connection can't be made.
- **Noise/TLS** — end-to-end encryption (the Relay only sees ciphertext).
- **Peer identity** — each Node's libp2p identity is signed by the Controller at
  join, so Nodes only accept connections from peers in the same Fleet.

Rendezvous is via the Registry: a Node publishes its multiaddrs into the KV;
peers read them and let libp2p establish the connection (direct, else relay).

### 5.5 Relay

A few self-hosted public-IP machines running a libp2p relay (and optionally
NATS leaf nodes). Only used when hole-punching fails — direct connections cost
nothing. Relay bandwidth is the main operational cost; budget for it.

---

## 6. Wire protocol & data model

### 6.1 NATS subjects (all scoped by `fleet_id`)

```
fleet.<fleet_id>.node.<node_id>.cmd          # req/reply: run Task, cancel, etc.
fleet.<fleet_id>.node.<node_id>.event        # node-emitted events (status, errors)
fleet.<fleet_id>.task.<task_id>.output       # streamed stdout/stderr + final result
fleet.<fleet_id>.transfer.<transfer_id>.signal    # peers exchange multiaddrs / ICE
fleet.<fleet_id>.transfer.<transfer_id>.progress  # bytes, rate, state
```

### 6.2 JetStream KV — Registry

Bucket per Fleet (e.g. `FLEET_<fleet_id>_NODES`), `key = node_id`, value =
Node record, with a short TTL refreshed by heartbeat (missed TTL ⇒ offline).

**Node record**
```json
{
  "node_id": "n_8f3a…",
  "name": "sherlock-gpu",
  "labels": ["gpu", "hpc"],
  "capability": {
    "os": "linux", "arch": "amd64",
    "cpu_cores": 64, "gpu": "4xA100", "ram_gb": 256,
    "disk_free_gb": 2000, "tools": ["python3", "bash", "rsync"]
  },
  "state": {
    "status": "online",            // online | busy | offline
    "load": { "cpu": 0.12, "mem": 0.41, "gpu": 0.0 },
    "running_tasks": ["t_…"]
  },
  "net": {
    "public_ip": "…", "nat_type": "symmetric",
    "multiaddrs": ["/ip4/…/udp/…/quic-v1/p2p/12D3Koo…"],
    "reachability": "direct"       // direct | relay
  },
  "version": "0.1.0",
  "last_seen": "2026-06-29T12:00:00Z"
}
```

**Task request** (Agent → Node `…cmd`)
```json
{ "task_id": "t_…", "kind": "shell",   // shell | python
  "code": "…", "cwd": "/work", "env": {}, "timeout_s": 3600 }
```

**Transfer request** (Agent → Controller / src Node)
```json
{ "transfer_id": "x_…",
  "src_node": "n_a", "src_path": "/data/big.h5ad",
  "dst_node": "n_b", "dst_path": "/work/big.h5ad",
  "options": { "compress": "zstd", "verify": "sha256", "resume": true } }
```

**Transfer progress** (`…progress`)
```json
{ "transfer_id": "x_…", "state": "transferring",  // pending|connecting|transferring|verifying|done|failed
  "bytes_done": 1234567890, "bytes_total": 9876543210,
  "rate_bps": 850000000, "path": "direct" }     // direct | relay
```

---

## 7. Key flows

### 7.1 Join

```
1. curl -fsSL https://get.pantheonos.…/fleet | sh -s -- --key pbk_xxx
   (script detects OS/arch → downloads the right binary → `fleet up --key …`)
2. Runner → Controller (HTTPS): present key.
3. Controller validates → returns:
     - fleet_id (= user id)
     - scoped NATS user JWT (perms: fleet.<fleet_id>.>)
     - signed Node identity (libp2p membership)
     - relay multiaddrs
4. Runner connects to NATS with the JWT; writes its Node record to the KV
   (heartbeat TTL); starts libp2p, advertises multiaddrs in the record, and
   reserves a slot on the relay(s).
5. Node appears in the Registry; the Agent can now see and command it.
```

### 7.2 Run a Task

```
Agent: run_on_node(node, code, kind) → publishes to fleet.<f>.node.<n>.cmd (req/reply)
Runner: spawns bash/python, streams stdout/stderr to fleet.<f>.task.<t>.output,
        returns exit status + captured result.
```

### 7.3 Transfer (A → B)

```
Agent: transfer(A, src, B, dst) → transfer_id
  1. Request lands on fleet.<f>.transfer.<x>.signal (coordinator, or directly to A & B).
  2. A & B read each other's multiaddrs from the Registry → libp2p QUIC stream
     (DCUtR hole-punch; Circuit Relay fallback).
  3. A streams the file to B: chunked, zstd-compressed (optional), sha256-verified,
     resumable.
  4. Both publish progress to fleet.<f>.transfer.<x>.progress.
  5. Agent + live-view UI subscribe to progress.
```

### 7.4 Heartbeat / liveness

The Runner refreshes its KV record every N seconds (TTL = 2–3×N). A missed
refresh ⇒ the record expires ⇒ the Node shows `offline` in the Registry, with no
extra bookkeeping.

---

## 8. Agent interface (Fleet toolset)

A dedicated toolset the Agent calls. Signatures (illustrative):

```python
# Observe
fleet_list_nodes() -> list[NodeSummary]          # from the Registry
fleet_node_info(node) -> NodeDetail              # capability + live state + running tasks
fleet_status() -> FleetOverview                  # counts, load, in-flight transfers

# Execute
run_on_node(node, code, kind="shell", timeout=3600) -> TaskResult
run_on_label(label, code, kind="shell") -> list[TaskResult]   # e.g. label="gpu"

# Transfer
transfer(src_node, src_path, dst_node, dst_path,
         compress="zstd", verify="sha256") -> transfer_id
transfer_status(transfer_id) -> TransferStatus
broadcast(src_node, src_path, dst_nodes, dst_path) -> list[transfer_id]
gather(src_nodes, src_path, dst_node, dst_dir) -> list[transfer_id]

# Manage
fleet_set_label(node, labels)
fleet_remove_node(node)
```

The Registry is surfaced to the Agent as a structured, queryable view (and to
the human as a live-view dashboard) so the Agent can make placement/transfer
decisions itself.

---

## 9. Security model

- **Per-Fleet isolation**: a Node's NATS JWT only grants `fleet.<user_id>.>`;
  Fleets cannot observe or touch each other. libp2p peer identities are signed
  per Fleet, so Nodes only accept connections from same-Fleet peers.
- **End-to-end encryption**: libp2p Noise/TLS on the data plane — the Relay sees
  only ciphertext.
- **Code-execution trust (be explicit)**: a Runner executes arbitrary
  Agent-issued commands **as the user, on that machine**. Running `fleet up` is
  the opt-in. Mitigations: per-Node enable flags, optional sandbox / resource
  limits (cgroups, ulimits), a full **audit log** of every Task, and one-command
  Node revocation.
- **Transfer integrity**: chunk + sha256 (Merkle) verification; resumable;
  reject on mismatch.
- **Key handling**: the raw API key is exchanged once at join for a narrowly
  scoped, rotatable NATS credential — Nodes never hold broad cluster creds.

---

## 10. Special environments

- **HPC (e.g. Sherlock)**: the Runner runs as a normal user process on a login
  node or inside a job. Outbound to NATS and to the Relay is typically allowed;
  inbound (hole-punching) usually is not → such Nodes use a **relay-only** data
  path, marked `reachability: relay`. For HPC↔HPC bulk data, a future Transfer
  backend can bridge to **Globus** (Stanford has endpoints).
- **NAT / firewalls (laptops, home, corp)**: DCUtR hole-punching gets a direct
  path most of the time; Relay covers the rest. No port-forwarding or VPN
  needed.
- **Cloud VMs**: usually directly reachable (public IP) → direct QUIC, fastest
  path.

The Agent always sees a Node's `reachability` so it can reason about expected
transfer speed.

---

## 11. Tech stack & language choice

| Layer | Choice |
|---|---|
| Runner / Controller | **Go** |
| Data plane | **go-libp2p** (QUIC via quic-go, DCUtR, Circuit Relay v2, Noise) |
| Control plane | **NATS** (`nats.go`) + **JetStream KV** + decentralized **JWT/accounts** |
| Execution | subprocess (`os/exec` → `bash`/`python3`); v2: managed Python toolset endpoint |
| Distribution | static binary (`CGO_ENABLED=0`), `GOOS/GOARCH` cross-compile, `curl | sh` installer |

**Why Go (not Rust):**
1. **go-libp2p is the canonical, most-mature libp2p** — DCUtR + Circuit Relay v2
   + QUIC are first-class and battle-tested (IPFS/Filecoin). The riskiest part
   (NAT traversal) uses the reference implementation.
2. **Best static-binary + cross-compile story** — go-libp2p and quic-go are pure
   Go (no CGO), so `CGO_ENABLED=0` yields a fully static binary for every
   OS/arch with one command. Exactly what "curl + run, all platforms" needs.
3. **The Runner is I/O glue** (NATS ↔ libp2p ↔ subprocess) — Go's sweet spot,
   fastest to build and maintain, low onboarding cost for a Python-centric team.

Rust would only win for line-rate, no-GC, minimal-footprint movers — none of
which a transfer bound by network + QUIC crypto needs. Go's GC pauses are
sub-millisecond and irrelevant to file streaming.

---

## 12. Phased rollout

Legend: ✅ done & verified · ◻︎ todo · 🔸 needs a decision (§13) or real infra.

**Phase 1 — MVP (the agent can see, run, and move data)**
- ✅ Controller: key → Fleet, relay list, **scoped NATS JWT** (per-fleet
  subject/KV/inbox isolation, `internal/auth`) + key gating — **hub-backed
  platform-key validation** (`--hub-url`; the hub mints/validates `pbk_` keys,
  pantheon-hub `feat/platform-api-keys`, pending prod-hub deploy) with an
  interim `--allowed-keys` fallback. A reference secured stack
  (Controller+NATS+relay) is deployed on DO — see `fleet/deploy/production.md`.
- ✅ Runner: NATS connect + JetStream KV registry/heartbeat + go-libp2p data
  plane (direct + relay fallback) + shell/python execution.
- ✅ 1–2 self-hosted Relays (relay-fallback path verified on real cross-region infra).
- ✅ Fleet toolset: `fleet_list_nodes`, `fleet_node_info`, `fleet_status`,
  `run_on_node`, `transfer`, `transfer_status` (+ Phase 2/3 tools below).
- ✅ `curl | sh` installer (OS/arch detection).

→ Outcome (met): any NAT'd machine joins with one command; the Agent sees every
Node, runs code on a chosen Node, and moves a large file A→B (direct or relayed).

**Phase 2 — robust & observable**
- ✅ Transfer **resume**; ✅ live progress; ✅ `broadcast` / `gather`. ◻︎ parallel streams.
- ✅ Live-view Fleet dashboard — `fleet-dashboard` (standalone Go + embedded page;
  `--creds` for a secured fleet). Product placement (hub/desktop) still open.
- ✅ Labels & label-targeted execution (`run_on_label`, `fleet_pick_node`).
  ◻︎ live label mutation (`set_label`); 🔸 audit log + Node revocation (tie to the security model).

**Phase 3 — reach & integration**
- 🔸 Globus/rsync Transfer backend for HPC↔HPC (needs HPC/Globus infra to verify).
- ~~Managed Python toolset endpoint on Nodes~~ — **dropped**: `run_on_node(kind="python")`
  and shell already cover running code on a Node; a managed interpreter adds little.
- ✅ Optional auto-placement (`fleet_pick_node`); ✅ data-plane tuning (zstd); ◻︎ multistream.

---

## 13. Open questions / decisions

- **Decided**: control/data plane split; Fleet glossary with "Agent" reserved;
  Runner in **Go**; data plane on **go-libp2p**; per-user Fleet keyed by API key;
  curl-installed single binary.
- **Open**:
  - Controller as a new service vs folded into the existing hub?
  - Relay topology/region(s) and bandwidth budget.
  - Exact NATS isolation: one account per user vs subject-scoped JWT in a shared
    account.
  - Sandbox depth for Task execution (process limits vs full container).
  - When to attach the full Python toolset endpoint to a Node (Phase 2 vs 3).

---

## 14. Appendix

### 14.1 Installer sketch

```sh
# fleet bootstrap (served from get.pantheonos.…/fleet)
os=$(uname -s | tr '[:upper:]' '[:lower:]'); arch=$(uname -m)
curl -fsSL "https://get.pantheonos.…/bin/fleet-${os}-${arch}" -o /usr/local/bin/fleet
chmod +x /usr/local/bin/fleet
exec fleet up --key "$1"
```

### 14.2 Naming cheat-sheet

> Agent (AI) • Fleet (the pool) • Node (a machine) • Runner (`fleet` binary) •
> Task (run code) • Transfer (move data) • Control plane (NATS) • Data plane
> (libp2p) • Registry • Controller • Relay.
