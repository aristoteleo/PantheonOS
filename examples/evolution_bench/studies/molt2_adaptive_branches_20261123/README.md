# Adaptive Molt2 development campaign

This records the live 40-trial campaign: AHC024 and AHC058, five seeds400–404, SimpleTES, vanilla Molt, target-writer ModifiedMolt, and Molt2AdaptiveBranches. Each uses the same150 public cases,80-call selection cutoff, GCC11, pinned DeepInfra/DeepSeek model and provider price limits. Per-run model soft cap is0.50USD; the existing cumulative math ledger has a74USD cap. No validation is scored in this campaign.

The native engine checkout remains at `b496461`. This records branch adds experiment files without changing the live checkout or its methods. Model/algorithm development is on `erwin/molt2-adaptive-branches-20261123`.

## Reproduction artifacts

The local study is `/home/erwinpi/molt2-adaptive-branches-lab`. `campaign.json` and `manifest.json` preserve its exact paths and arguments. They are an original-run record: relocate these paths and provision a separate ledger/output directory before a new launch. `LAUNCH.json` intentionally preserves the existing launch marker. Do not remove it from the live workspace to start another campaign.

`research/SOURCE_BACKUP_RECEIPT.json` identifies the checksum-verified private Modal bundle (`molt-cardiac4d-runs`). It includes the frozen source, wrappers, exact300 public inputs, both tester binaries and recorded GCC executable; `research/runtime-assets/MANIFEST.json` maps313 original runtime paths to archived files and hashes. Use matching GCC11/system libraries; the saved GCC executable alone is not a portable toolchain. API credentials are supplied separately and are absent from this record.

Completed-run `research/BACKUP-*.json` receipts identify private archives containing the full store, method state, selected source and request/response traces. Incremental backups run automatically. The finalizer saves a complete campaign archive and a concise report when the searches terminate.

## Analysis boundaries

The first completed seed is diagnostic, not superiority evidence. Five paired seeds are summarized separately per task; incomplete groups are withheld. Reserved validation remains untouched. The native evaluator call counter can overshoot80 with concurrent workers; selection explicitly discards measurements beyond the common80-call ceiling.

`research/receipt-fix/AMENDMENT.json` records a financial-only change for future trials. First4 started trials retain the original guard. Later trials shield admitted requests through a bounded300-second receipt drain, forbid new requests after search, and discard late responses before optimizer feedback. Old uncertain charges remain conservatively booked. Native algorithms, prompts, data and selection limits are unchanged.

Report confirmed provider charges separately from uncertain holds. Native elapsed time and process wall time can include financial cleanup; `run_resource_audit.py` also derives time to the80th recorded evaluation from event timestamps. Search scores remain timing-sensitive because generated C++ programs use wall-clock limits.

After all optimizer processes terminate, `reread_finalists.py` checks each frozen selected source on the same150 public cases once per original CPU slot. Identical source hashes share rereads; selection stays frozen and no model calls or validation cases are used. Failures/timeouts are reported explicitly. These public robustness rereads supplement, and never replace, the primary first80-call search results.
