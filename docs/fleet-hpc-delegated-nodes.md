# HPC delegated nodes: phase 2 checkpoint

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
