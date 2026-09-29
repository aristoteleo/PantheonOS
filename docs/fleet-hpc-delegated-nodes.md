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

Deploy the Controller first (new `/delegate` endpoint), then the connector Fleet
runner, Agent/Fleet toolset, and UI. The Mac connector was updated locally to
`0.5.0-hpc.2-dev` on September 29 for keep-connected acceptance. The Controller
delegation endpoint was deployed and health checked on staging. The Agent image
build is still in progress, so its new tool and UI metadata have not yet been
accepted live. Sherlock allocation 45849347 registered as a separate node and
ran Python and shell code on sh02-01n32.int with the correct SLURM_JOB_ID.
File write/read/list, traversal rejection and test-file cleanup passed. Cancelling
the job succeeded and removed the delegated registry entry. Sign-out/re-sign-in
recovery has unit coverage but was not repeated with this live allocation.

The one-second timeout check exposed insufficient transport startup allowance:
the task failed, but SSH was killed before a structured timeout could return.
The follow-up reserves 30 seconds outside the user code timeout for Slurm startup
and teardown, and gives HPC RPCs a matching 35-second response allowance. The
regression tests pass; this follow-up still needs connector deployment and a live
retest. Atrium lost its workspace node during acceptance, blocking the final UI
check independently of the successfully exercised HPC RPC path.

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
