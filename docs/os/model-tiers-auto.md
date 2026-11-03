# Platform model tiers, chosen every week

An Agent's quality tiers are the Model Services routes `tier-low`,
`tier-normal` and `tier-high` (`fleet-route://tier-<name>`). The Hub chooses
the platform default for each tier once a week from public data and our own
measurements, applies it to every owner who has not edited that tier, and keeps
each version so one action restores the previous one.

## Where the tiers live

A tier route is a per-owner row in the Hub's `model_routes` table, owned by
the same boundary as the owner's model services. Nothing platform-wide existed
before this job:

- The release profile carries `DEFAULT_TIERS` (`general_team/team.py`) as the
  seed chain of the platform Connector App (`apps/model-service/directory.py`).
- When an owner's platform Connector first registers, it creates each missing
  tier route over its own `platform` deployment (revision 0 → 1). An existing
  route is never overwritten; later saves bump the revision (an owner's edit,
  or the Connector following its service to a new node).
- The Agent's model policy names the tier routes at `current`, so the
  controller's hosted model-access service (`os/internal/hosted/modelaccess.go`)
  resolves whatever the row says at call time. The controller has no
  cross-owner view and no database; it only reads routes through the Hub.

So tiers are per-owner copies seeded from a release default. The revision
cannot tell an owner's edit from the Connector's node move, but the candidate
list can: the Connector only ever writes the seed chain, and node moves change
`allowed_nodes`, not candidates.

**An owner follows the platform default for a tier** when the route's
candidates are all on the `platform` deployment and their model list is exactly
a chain the platform has used for that tier (the release seed, or any recorded
version). Anything else is the owner's version and is never touched. Saving the
current platform chain again ("Reset to platform default" in Model Services)
makes an owner follow it again. An owner who hand-picks exactly a former
platform chain counts as following; that is the only ambiguity, and it errs
towards the platform default the owner chose.

## Who runs the job

The Hub. It owns the rows the job writes (routes and the `platform`
deployment's published models), already holds the LiteLLM master key and
in-cluster proxy address, mirrors the OpenRouter catalog, and runs background
loops. The controller would have to call back into the Hub for every owner to
do the same writes.

## Data

| Input | Source | Use |
|---|---|---|
| Callable models | LiteLLM proxy `/v1/models`, joined with the models the owners' `platform` deployments publish | Pool |
| Context, tools, modality | OpenRouter `/api/v1/models` (the Hub's cached mirror) | Filters |
| Price | Our LiteLLM spend per model (14 days), else OpenRouter `/api/v1/models/{id}/endpoints` | Effective price |
| Quality | Artificial Analysis `/api/v2/language/models/free` (`artificial_analysis_intelligence_index`, with `intelligence_index_version`) | Frontier, tiers |
| Usage | OpenRouter `/api/v1/datasets/rankings-daily`, last 7 days, keyed by `canonical_slug` | Tie-break, low tier without quality data |
| Health | LiteLLM `/user/daily/activity` (all keys), last 7 days: successful / failed requests per model | Error-rate filter |
| Probe and smoke eval | A 1-call probe and three tool-calling tasks through our proxy | Final check of each chosen model |

Keys: `ARTIFICIAL_ANALYSIS_API_KEY` and `OPENROUTER_API_KEY` from the Hub's
environment (Kubernetes secrets). They are sent only to their own hosts and are
never logged or written into reports. Artificial Analysis allows only a few
requests a day (one per page), so the Hub keeps its last successful fetch
(`platform_tier_sources`) and fetches again at most every 6 days; a failed
fetch falls back to the stale copy and the report says so.

Matching quality to our ids uses a key of vendor + family + version: the last
path segment, lower case, dots as dashes, without a variant suffix (`:batch`,
reasoning-effort words such as `high` or `non-reasoning`) and without dates
(`-1017`, `-20250929` are snapshots, not versions). Artificial Analysis lists a
model once per reasoning setting; the key takes the best reported score.
Vendors are compared through a small alias table (`z-ai` ↔ Z AI, `qwen` ↔
Alibaba, ...). A model without a match has no quality score.

## Selection

All thresholds are in `TierPolicy` (`pantheon_hub/services/platform_tiers.py`);
the defaults:

1. **Filters.** Text in and out; `tools` in `supported_parameters`; context
   ≥ 128k; price known and not zero; not `:free`, `:batch`, `~…` or `-latest`
   aliases, preview or experimental; 7-day error rate under 20 % once a model
   has 20+ requests. Among ids with the same key, a dated (pinned) id is
   preferred.
2. **Price** = what we would pay per million tokens. Where LiteLLM's spend
   logs (`LiteLLM_SpendLogs`, without this job's own calls) have 100+ calls
   in 14 days, our observed cost per token, caching included. Otherwise the
   price at the provider our routing uses: the first provider of our LiteLLM
   `openrouter/*` route's `extra_body.provider` order (read from the mounted
   LiteLLM config, ignore list honoured) that serves the model, else the model
   maker's own endpoint from `/models/{id}/endpoints`. Cheap third-party hosts
   are not used by default; only a model without a maker endpoint falls back to
   the median healthy, tool-capable provider (labelled). Prices are cache-aware with our measured token mix
   (cached input / fresh input / output over 14 days of our OpenRouter
   traffic; 70/20/10 without data); on Anthropic models fresh input is a
   cache write. The catalog's 3 : 1 blend is the last resort. The report names
   the basis of every price, the mix and the routing it used.
3. **Frontier**: models with a quality score that no cheaper-or-equal model
   matches or beats.
4. **high**: the best quality on the frontier with price ≤ $12/M.
   **normal**: walk up the frontier from its cheapest model while each step
   adds ≥ 5 index points per $1/M of effective price, within $3/M
   (`normal_rule='marginal'`; `'knee'`, the best quality per dollar above 60 %
   of the top quality, remains available).
   **low**: the cheapest model with ≥ 40 % of the high tier's quality.
   Quality within 1 point counts as equal; ties go to higher OpenRouter usage,
   then a lower error rate.
5. **Chains**: primary + 2 fallbacks in the same order of preference, from
   other vendors first. Fallback is `failover`, as before.
6. **Stability**: a tier keeps its current primary unless the new one is better
   by a margin (+5 % quality at ≤ price, or −30 % price at ≥ quality; for high,
   the best-quality tier, +5 % quality within its price cap even when it costs
   more), or the
   current one fails a filter, the probe or health, or has no quality score
   while the new one has. A tier changes at most once per run. Fallbacks that
   fail a filter are replaced even when the primary stays.
7. **Checks**: every model in a new chain must answer the probe; a new primary
   must pass the smoke eval (call a tool with the right arguments, use the
   tool's result, pick the right one of two tools) and have evidence beyond it:
   20+ real calls through our proxy in 14 days, or a larger eval (the smoke
   tasks three more rounds, all passing). A model without that evidence can
   still enter a chain as a fallback. A failing model is skipped.

**Without quality data** (no key, or the API fails) the run is degraded and
says so: high and normal keep their current chains (nothing is chosen without
quality evidence); low may move to the cheapest model that is in OpenRouter's
weekly top models by usage and passes the probe and smoke eval.

## Versions, apply and rollback

Every real run writes a row in `platform_tier_runs`: the resulting chains, the
report (inputs per candidate, frontier, choice and reason per tier, data
attribution with as-of dates) and the previous row. The first row is the
release seed, so the first real run can be rolled back. A run that changes
nothing still records its report but leaves the chains as they were (T5).

Applying writes each following owner's tier route candidates (other route
fields stay) and, when a chosen model is not yet published by that owner's
`platform` deployment, adds its entry there (copied from an owner whose
Connector published it; the Connector keeps entries it can still serve).

A tier route requires tool calling, so a chain model an owner publishes with
tool calling unconfirmed (`tools: null`, as the Connector published the
release's tier models before the profile carried their capabilities) cannot
resolve: the Agent reports the tier as unavailable. Applying states the
capabilities another owner's platform service confirmed for the same model,
also for owners whose chain is already current.

Rollback writes a new row with the chains of the latest earlier row whose
chains differ, and applies it: owners who followed the default get exactly the
previous chains back. Models added to `platform` deployments stay published.

## Schedule and admin

`PLATFORM_TIERS_SCHEDULE=weekly` enables the Hub loop: every hour it checks
whether the last real run is a week old (one row per ISO week, so replicas
cannot both apply), and otherwise brings owners who follow the default up to
the current version (new owners are seeded with the release chain). Unset, the
job runs only by hand.

Admin endpoints (admin bearer token):

- `POST /api/admin/platform-tiers/run` `{"dry_run": true}` — the report, no
  writes. `dry_run: false` applies; `user_ids` limits the apply to those owners
  (for trials; the version is still the platform's current one).
- `POST /api/admin/platform-tiers/rollback` `{"dry_run": false, "user_ids": …}`.
- `GET /api/admin/platform-tiers` — current version and recent runs.

Owners see, in Model Services → Routes, whether a tier follows the platform
default, when it was chosen and why, the data attribution, and a "Reset to
platform default" action (`POST /api/model-services/routes/tier-<name>/platform-default`).
The Hub lists the status beside the routes (`platform` in the listing), not
inside them, so a route is still saved back exactly as it was read; the
controller's model-management App passes it through and forwards the reset
(`model_services_routes` action `platform_default`).

Attribution: quality data from Artificial Analysis
(artificialanalysis.ai); usage from OpenRouter (openrouter.ai/rankings), as of
the dataset's `meta.as_of`, licensed CC BY 4.0.
