# Fleet security model (P0: no single-point-of-failure)

Status: design + rolling implementation. Root of trust is the user's **platform
account** (OAuth, revocable, MFA-able) — not a static key.

## Why the current model fails "one leak → whole network"

Today a single long-lived per-user key (`pbk_…`) does everything:

1. **One long-lived key for everything** — it both `fleet up`-joins nodes and is
   the agent's control credential. Leak = permanent full access.
2. **Shared across all nodes + the agent** — no per-node / per-principal identity.
   At the NATS layer every node's cred can pub/sub on all `fleet.<fid>.>`
   subjects, so any cred can command any node.
3. **Bearer token = authority** — possession of the bytes = you. No proof of who.
4. **No expiry / rotation** — leak is permanent until a manual revoke, which
   nukes the whole fleet.
5. **Node executes any command from any fleet-cred holder** — `run_on_node` is
   RCE for anyone in the fleet.
6. **The key is passed around** (UI, join command, env) — many exposure points.

## Target: credential types

| Credential | Held by | TTL | Can do | If leaked |
|---|---|---|---|---|
| **platform session** | user (browser / backend) | OAuth standard | authorize the credentials below | account-level; MFA + revoke |
| **join token** | transient (adding a node) | 15 min, **single-use** | add exactly **one** machine to a fleet | short + one-shot; an unexpected node appears → detectable |
| **node key** | node, on disk (private) | permanent, **never leaves the node** | prove "I am this node" | only on that machine (same trust boundary) |
| **refresh token** | node, on disk | long (e.g. 30d) | **only** mint access creds, and **only with a node-key signature** | useless alone (no node key) |
| **node access cred** (NATS JWT) | node | short (1h) | connect NATS, receive/exec commands, transfer; **only this node's subjects** | ≤1h window, one node |
| **agent control cred** (NATS JWT) | sandbox / local backend | short (1h) | send commands, read registry; derived from the session | ≤1h; dies with the session |

## Sequences

### 1. Add a node (join) — single-use token, node generates its own keypair
```
UI/user        platform          controller             fleet up (node)
  |── add node ──►│ (session auth)    │                      │
  |               │── POST /join-tokens ─►│ mint 1-use token(15m)│
  |               │◄── join_token ────────│                     │
  |◄─ cmd: fleet up --join-token <tok> ───┘                     │
  |                                    │        gen keypair (priv stays local)
  |                                    │◄── POST /join ──────────┤
  |                                    │   token + node_pubkey    │
  |                                    │ verify token (consume) + register pubkey
  |                                    │── refresh_token + access cred ─►│
  |                                    │                          connect NATS
```

### 2. Node refresh — the legit node is always online; only leaked creds are short
```
fleet up (node)                     controller                 NATS
  │ access cred used to ~75% TTL (~45m of 1h)                   │
  │── POST /token ────────────────────►│                        │
  │   refresh_token + sign(challenge, node_key)│ verify signature (proof-of-possession)
  │                                    │ + refresh_token valid + not revoked
  │◄── new access cred (1h) + rotated refresh ─┤                │
  │── reconnect with new cred (sub-second) ────────────────────►│
  │   re-subscribe command subjects; transfers resume; user sees nothing
```

### 3. Agent runs a command — separate control cred + node-side authz
```
agent (control cred)        NATS                 node N (access cred)
  │── publish cmd@node.N ───►│ validate agent JWT (scope = control)
  │                          │── deliver ──────────────────────►│
  │                          │                     check sender principal
  │                          │                     + policy (may run?) + audit
  │◄── result ───────────────│◄──────────────────────────────────┤
```

### 4. Revoke / containment — worst-case exposure = remaining TTL, not forever
```
UI ── revoke node N ─► platform ── POST /revoke ─► controller
                                              mark node/refresh revoked (+ push NATS deny)
  node N's next /token fails → its access cred expires within ≤1h → offline
```

## Controller HTTP interface

| endpoint | auth | in | out |
|---|---|---|---|
| `POST /join-tokens` | platform session | `{fleet, capability?}` | `{join_token, expires_at}` |
| `POST /join` | join_token | `{node_pubkey, name, capability}` | `{node_id, refresh_token, access_cred, nats_url}` |
| `POST /token` | refresh_token + node signature | `{node_id, refresh_token, challenge_sig}` | `{access_cred, expires_at, refresh_token?}` |
| `POST /agent-cred` | platform session | `{fleet, scope}` | `{access_cred, expires_at}` |
| `POST /revoke` | platform session | `{node_id \| token_id}` | `{ok}` |
| `GET /nodes` | agent-cred / session | `?fleet` | `{nodes}` |

## fleet up state dir (`os.UserConfigDir()/pantheon-fleet/`)
```
node_id
node.key        # Ed25519 private key, 0600, NEVER leaves
refresh.token   # 0600
access.creds    # 0600, short-lived, rewritten by the refresh loop
```
- `fleet up --join-token <tok>` the first time; afterwards plain `fleet up`
  refreshes with the stored refresh_token + node.key (no token needed again).
- Background **refresh loop**: renew at 50–75% of TTL; a 401 from `/token` means
  the refresh was revoked → re-join required.

## Why "one leak ≠ whole network"

- **Stolen access cred**: expires in ≤1h, scoped to one node's subjects.
- **Stolen refresh token**: useless — `/token` requires a signature from the
  node key, which never leaves the node (**proof-of-possession**). Grabbing the
  refresh token from logs/network doesn't let an attacker mint creds elsewhere.
- **Stolen join token**: single-use, 15 min, adds one node, and the new node is
  visible → detectable.
- **One node compromised**: only that node's key + creds (already on that box).
  Other nodes are unaffected.
- **Root of trust** is the revocable, MFA-able platform account, not a static
  string.

## Per-node NATS scoping (transport-level least privilege)

`MintFleetUser` currently grants fleet-wide `fleet.<fid>.>`. Move to per-node
scoping so a node access cred can only:
- subscribe to **its own** command subject `fleet.<fid>.node.<node_id>.>`,
- publish results / progress on its own reply subjects,
- (nodes do NOT need to command other nodes).

The **agent** control cred keeps the broader publish scope (it addresses any node
+ reads the registry). So a node cred leak can't command siblings.

## Migration (no break)

1. Keep `pbk_` working as a long-lived join-token equivalent during transition.
2. New joins use `--join-token`; nodes gain a keypair + refresh token.
3. Agent control cred moves to session-derived (`/agent-cred`).
4. Deprecate, then remove, `pbk_` as a control credential.

## Implementation roadmap (increments)

- **A. Short-TTL access creds + refresh plumbing** — `MintFleetUser` sets
  `Expires`; add `/token`; fleet up runs a refresh loop. (Backbone; refresh auth
  is still the key at this step — no security gain yet, just machinery.)
- **B. Per-node keypair + proof-of-possession refresh** — fleet up generates an
  Ed25519 key, sends its pubkey on join; controller issues a refresh token bound
  to the pubkey; `/token` verifies a node-key signature. Per-node NATS scoping.
  **(The real security win.)**
- **C. Single-use join tokens** — `POST /join-tokens`, one-shot 15-min tokens;
  `fleet up --join-token`.
- **D. Agent control cred from the platform session** — `POST /agent-cred`;
  FleetToolSet stops using `pbk_`.
- **E. Node-side authz + audit (+ optional approval)** and **revocation list**.
