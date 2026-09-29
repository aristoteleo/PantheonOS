# HPC delegated nodes: phase 2 checkpoint

> Current checkpoint, 2026-09-29: attended submissions now require an actual
> HTTP App workload. The batch job starts it immediately; Fleet attaches the
> existing App without launching it again. The signed Mac connector is
> `0.5.0-hpc.5-dev` (source `9600ab7b`). Sherlock accepted live job `45940665`
> (normal, 1 CPU, 1 GB, 20 minutes); end-to-end acceptance is in progress.
> Earlier holding-allocation behavior below is historical, not the current
> launch path.

## Implemented in this change

Each new session-mode Slurm job receives a random allocation generation, stored
in its private local record and the Slurm job comment. The connector represents
that allocation as a distinct node in the existing Fleet registry. Its node ID
is derived from fleet, connector and generation; neither job ID recycling nor a
second connector can accidentally select the same identity.

The Controller `/delegate` endpoint verifies the connector's refresh token,
revocation status and an operation/allocation-bound Ed25519 proof. It mints a
normal single-node NATS credential, without broadening the connector credential.
Credentials remain on the connector. Child credentials are short lived; parent
revocation blocks renewal (existing credentials expire at the normal access TTL).

The connector serves the child's standard `ping`, `app_list`, and `run_task`
subjects. Tasks execute inside `srun --jobid ...` steps, never on the login node.
One operation runs at a time per allocation. Task results retain at most 32 KiB
per output stream and indicate truncation. Tasks have a bounded timeout and
terminate their process group, including descendants, when finished or timed out.

`hpc_file` and the Agent's `hpc_workspace` tool provide list/mkdir/read/write.
File paths resolve under the allocation workspace, including symlink checks.
Reads are chunked; writes are bounded to 64 KiB and do not overwrite existing
files unless explicitly requested. This path boundary is not a sandbox for
arbitrary user-authorized shell/Python code, which runs as the user's HPC account.

No Fleet daemon is installed on the cluster. A small embedded Python program is
sent as the job step command; Python 3 and bash must exist in the compute environment.

## Login and scheduling semantics

- Default SSH idle limit: 30 minutes; configurable up to 12 hours.
- Explicit `keep_connected` disables Fleet idle sign-out until manual sign-out
  or connector shutdown. Existing SSH keepalives run every 60 seconds, with
  three missed replies detecting a dead connection. Cluster-imposed expiry and
  Duo authentication remain unchanged; no synthetic remote work keeps it alive.
- Merely leaving the Fleet page open no longer refreshes the login lease.
- Passive job monitoring does not refresh the lease. Actual commands and file
  operations do. Active operations prevent idle expiry until they finish.
- No automatic reauthentication or Duo approval. SSH operations must reuse the
  attended ControlMaster and cannot fall back to a new connection.
- Job queries, including failures, are cached for a minute per cluster.
- Transport/query failure means unknown, not completed. Accounting lag also
  means unknown. A recycled job ID is rejected by its allocation comment.
- Cancelling checks the allocation comment before invoking scancel.
- Signed-out nodes refuse operations, retain their identity, and can recover
  after sign-in. Confirmed terminal allocations are removed from live discovery.
- Existing jobs created before this change have no allocation marker and are
  not automatically adopted. Submit a new small job for acceptance.

## Validation

Automated tests cover encrypted SSH session reuse and idle accounting, bounded
remote task/file execution, timeout/process cleanup, traversal/symlink checks,
recycled job IDs, passive polling limits and failed-query semantics, delegated
proof scope and revocation, and real JWT-authenticated NATS discovery/task/file
round trips. The NATS integration test replaces SSH/Slurm with local fixtures;
it is not evidence of real Sherlock scheduling or execution.

## Deployment and remaining work

The first phase 2 milestone is deployed and accepted on staging (September 29).
The Controller delegation endpoint is healthy; the signed Mac connector runs
`0.5.0-hpc.3-dev`, and the Agent runs `docker.io/nanguage/pantheon-agents:sha-c27cbe8`
(source `c27cbe8990f81c5fc9b0fc1606f57f8f117d6af8`). The Agent update retained the
pod identity, and the restarted connector reused the attended SSH master.

Real Sherlock acceptance used two bounded, single-CPU, 1 GiB allocations:

- Job 45849347 registered independently and executed Python/shell on
  `sh02-01n32.int` with the correct SLURM_JOB_ID. File write/read/list, traversal
  rejection and file cleanup passed. Cancellation removed the delegated node.
- Job 45853404 ran on `sh02-01n07.int`. Pending allocations rejected execution;
  the corrected one-second task timeout returned a structured `timeout` with
  exit code -1, and subsequent execution succeeded. File checks passed again.
  The actual deployed Agent FleetToolSet successfully exercised `run_on_node`
  (timeout and success) and `hpc_workspace`. The job ultimately reported
  COMPLETED, and its registry entry disappeared. Neither test allocation remains
  active.

The first timeout check found that SSH transport could expire before Slurm startup
and a structured result completed. The deployed fix reserves 30 seconds outside
the user-code timeout and gives HPC RPCs a matching 35-second response allowance;
it does not increase the requested code execution timeout.

After the user resolved the Modal workspace spend limit, Atrium reconnected.
Browser acceptance showed the separate HPC compute node, Slurm job ID, connector
and real compute hostname. The current local UI also hides model deployment for
delegated nodes that do not advertise app lifecycle support; a Vue regression test
covers that capability gate. Sign-out/re-sign-in recovery has unit coverage but
was not repeated during this live allocation test. This milestone does not claim
live resource telemetry or full application hosting.

The proxy advertises only the capabilities implemented here. It deliberately
does not advertise `app-lifecycle`, a terminal service, the Files app adapter,
GPU model serving, a data-plane transfer endpoint, or container execution.
Remaining phase 2 milestones:

1. Remote package/cache/data storage plus process lifecycle driver and receipts.
2. Attended compute-service port forwarding into the existing App gateway.
3. Files app and bulk transfer adapters; large uploads and durable task receipts.
4. Model Services deployment and GPU acceptance, then additional runtimes.

Do not widen node capabilities before the corresponding end-to-end path works.

## Keep-connected acceptance (September 29)

Added a per-cluster, persisted `keep_connected` option. Old profiles retain the
30-minute default unless enabled explicitly. The watcher still probes the SSH
master and detects real disconnects. The UI validates the returned policy so an
older connector cannot silently claim that it supports this option.

Go race tests cover idle timeout, 24-hour simulated inactivity with keep-connected
enabled, and dead-master detection. Vue tests cover policy persistence and normal
password/Duo handling. Type checking and scoped ESLint passed. A signed Mac bundle
was installed with the existing Developer ID after the user enabled App Management;
the previous bundle is backed up at `/tmp/pantheon-hpc-backup-20260929.app`.

Browser acceptance succeeded against Sherlock with user-approved Duo: the new
connector reported signed in with keep-connected enabled and returned Slurm
partitions. Temporarily setting the fallback idle threshold to one minute for
95 seconds did not expire the session; reopening the view fetched a fresh
connected status. The original 30-minute fallback was restored. This verifies
Fleet's idle-policy exemption, not an indefinite cluster-side session guarantee.
No Slurm allocation was submitted for this test.

## Allocation HTTP services (legacy transport semantics)

`hpc-services: 1` adds an explicit `hpc_service` protocol (version 1) with
`start`, `list`, and `stop`. This is a command-based HTTP service driver, not the
full package lifecycle: `app-lifecycle` remains absent. A start specifies a name,
argv array, workspace-relative cwd and a 1–600 second startup limit. `${HOST}`,
`${PORT}` and `${WORKSPACE}` are expanded on the compute node; HOST/PORT are also
provided as environment variables. The application must bind its assigned
loopback port. Files can be staged through the existing bounded workspace API.
The default UI command serves the chosen workspace directory over HTTP.

One service may run per allocation, using its allocated CPU/memory/GPU resources.
Separate `run_task` operations are refused while it is active; file operations
remain available. A persistent Slurm step contains an ephemeral Python stdlib
supervisor. No Fleet credentials, daemon, public listener, SSH compute login or
additional Python dependencies are needed. TCP frames are multiplexed through
the attended SSH step, so HTTP and WebSocket traffic do not start new Slurm steps.
Connections and buffers are bounded, with receiver credits for large responses.

The connector records service identity, spec digest, generation, state and bounded
logs privately on disk. Exact duplicate starts are idempotent; start/stop reject
stale generations. Only a running exact binding can be opened through the existing
Hub-authorized App gateway. The proxy supports the gateway's lifecycle `service`
verification, not package installation. Gateway traffic cannot select arbitrary
hosts or ports. Stop, process exit and allocation/SSH loss close all streams.

The supervisor reaps the application's process group on EOF or termination. If
transport disappears without EOF, a 30-second heartbeat lease stops it. Slurm
also confines the process to the allocation lifetime. Interrupted receipts wait
40 seconds before allowing another start; connector restarts never automatically
restart a previous command. A user can inspect and start a new generation.

Local acceptance covers real JWT-scoped NATS plus App gateway round trips,
revocation of a stopped generation, repeated starts, stale stops, restart receipts,
startup timeout, connector cancellation, repeated connections and a 2 MiB response
to a slow reader. SSH/Slurm are replaced only in these local tests; live deployment
and Sherlock acceptance are recorded separately when completed.

### Live deployment and submission finding (2026-09-29)

- Runtime `978c62125dd2e6e6e1241d9e504bf6fbe5e425a8` built and passed
  the container packaging check (GitHub Actions run `36620680836`).
- Current Agent updated in place to `nanguage/pantheon-agents:sha-978c621`,
  preserving Pod UID and Workspace. The installed `apps/client.py` digest
  was checked after readiness. Mac runs `0.5.0-hpc.4-dev` from the same source.
- Sherlock authenticated successfully, with keep-connected enabled.
- A single bounded request (normal, 1 CPU, 1 GB, 20 minutes) was rejected
  before job creation. A read-only `sbatch --test-only` of that request
  recovered the full reason: sleep jobs artificially hold resources and are
  not allowed. No alternative placeholder or duplicate job was submitted.
- Detailed receipts are under
  `acceptance-2026-09-21/hpc-services-20260929` in the parent design workspace.

### Required job-first redesign

Resource requests must include the actual command/App, not create an empty
node and wait for work. For an HTTP App, the batch script starts that App
immediately on the allocated compute node. Fleet exposes its allocation and
exact service binding after readiness. The attended connector forwards only
that service; reconnecting must never restart user work implicitly. Stopping
the primary App cancels its own Slurm job, and restarting creates a new job
and binding. Batch commands can use the same job-first submission path.

The existing arbitrary-command service transport is reusable plumbing, but
should not be presented as a finished Sherlock App launch path. The Jobs UI,
submission API, persisted job metadata, service attachment and cancellation
need to change together. Jupyter/Model Services integration follows acceptance
of a real HTTP App job; GPU resources are unnecessary for that first test.

## Primary HTTP App jobs (current submission path)

An attended `hpc_cluster submit` now requires `request.service` with `name`,
`argv`, workspace-relative `cwd`, and `startup_seconds` (1–600, default 60).
Commands are argv arrays, not implicit shell programs. An empty resource-only
submission fails before contacting the cluster. Native Fleet-on-Slurm launch
behavior is unchanged for clusters that permit it.

The stdlib batch program starts the real HTTP App in the Slurm cgroup and
writes a private, atomic readiness receipt containing the allocation, job ID,
spec digest, assigned loopback port and child PID. App output goes to the normal
Slurm log. Failure to bind the port ends the job; no idle placeholder holds
resources waiting for an eventual command.

The connector advertises `hpc-job-service: 1` after compute readiness and attaches
an attended forwarding step with 1 CPU/256 MB and no GPU GRES. It accepts only
the primary receipt's matching job/allocation/spec identity and assigned port.
The App continues when forwarding disconnects; the forwarding step expires on
EOF/heartbeat loss. Reattachment uses the same generation and never launches or
kills the primary App. A changed command requires a new job, allocation and
binding. Arbitrary tasks are disabled on primary App nodes; bounded file
operations remain available.

`hpc_service stop` validates the exact primary binding, verifies the recorded
Slurm allocation comment and uses `scancel` on that job. It then closes its
forwarding streams. This releases Slurm resources instead of leaving an empty
allocation. The UI submits App and resources together, hides the independent
start form for primary nodes, and labels the terminating action **End job**.

Validation includes a real local Python HTTP workload retained across two
forwarding sessions (same child PID), stale/changed primary identity rejection,
no workload termination on forwarding-only close, and eventual batch termination.
The JWT/NATS control-plane fixture also verifies primary restart refusal, stale
stop refusal and cancellation of exactly the owned Slurm job. Go race tests across
six relevant packages, 18 Fleet Vue tests, Vue type checking and scoped ESLint pass.
The deployed Agent API from `978c621` already passes the structured workload and
service protocol through; no Agent/Workspace restart was needed for this update.
