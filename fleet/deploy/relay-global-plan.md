# Global relay plan — "balanced 5" (EXECUTED on prod 2026-07-07)

**Status: LIVE on prod.** The balanced 5-relay layout with geo-aware ordering is
deployed and verified (US node → US relay, EU → Frankfurt, CN/IN → Singapore, etc.).
China-specific enhancements remain **deferred** (documented at the end).

## Prod relay inventory (live)

| Region | IP | DO droplet | peer id (short) | continent |
|--------|----|-----------|-----------------|-----------|
| US-West (SFO) | 24.199.99.134 | 581354476 (on the controller droplet) | 12D3KooWA7CQ… | NA |
| US-East (nyc3) | 104.131.165.107 | 582822940 | 12D3KooWRCrrx1ZG… | NA |
| Europe (fra1) | 46.101.126.75 | 582822946 | 12D3KooWS4p2JEwP… | EU |
| SE Asia (sgp1) | 188.166.231.123 | 582822951 | 12D3KooWGDwCXqpo… | AS |
| South Asia (blr1) | 168.144.208.203 | 582822964 | 12D3KooWC8jxvnTQ… | AS |

geo-controller live on the prod controller (24.199.99.134); all 5 in its `--relays`
(zz-hub-url.conf drop-in; backup `.bak`). 4 new droplets = **+$24/mo**. Existing prod
nodes keep their current relay until they re-join; new/re-joined nodes get the
geo-sorted 5. **Note:** CN + IN both currently resolve to sgp1 (continent-level can't
rank sgp1 vs blr1 — both AS); the lat/long v2 would split them. AU/other regions with
no same-continent relay fall back to the operator order (harmless).

---

### Original plan (below, for reference)

Decision was: roll out the **balanced 5-relay** layout for global users, on **prod**.

## Why 5, and why these regions

Relays do **rendezvous + hole-punch coordination + data fallback** — NOT the direct
data path (DCUtR upgrades most transfers to a direct P2P connection, so the relay
drops out). So relay count is driven by **coverage of populated regions +
redundancy**, not by scale. With the geo-aware controller ordering
(`internal/relaygeo`) + go-libp2p `desiredRelays:2`, each node reserves on its
**nearest ~2** relays (local + a cross-region backup, automatically).

| # | Region | DO region | Serves |
|---|--------|-----------|--------|
| 1 | US-West | `sfo3` | NA west; **prod already has this** (24.199.99.134) |
| 2 | US-East | `nyc3` | NA east; better hop to/from Europe |
| 3 | Europe | `fra1` (Frankfurt) | EU / central Europe |
| 4 | SE Asia | `sgp1` (Singapore) | SE Asia + partial East Asia |
| 5 | South Asia | `blr1` (Bangalore) | India |

Every populated continent gets a same-continent relay; every node gets a local
relay + a cross-region fallback. 3 (US/EU/Asia) is the lean floor; 5 is the sweet
spot; >7 adds little (relays aren't accelerators).

## Cost

- **$6/mo per relay** (DO `s-1vcpu-1gb`, 1 vCPU / 1 GB, 1 TB transfer included — a
  relay is rendezvous-heavy / bandwidth-light, so this size is plenty).
- Prod already has SFO, so **incremental = +4 relays = +$24/mo**; total 5 = $30/mo.
- Bandwidth: relay traffic is mostly rendezvous (tiny). Only hole-punch **fallback**
  data flows through a relay (capped 5120 MB/dir/conn). DCUtR upgrades most transfers
  to direct, so relay bandwidth normally stays well under the included 1 TB → **~$0**.
  Heavy fallback would incur DO overage ~$0.01/GB.

## Execution runbook (when you say go)

Same recipe validated on staging 2026-07-06 (geo-controller + a real Singapore relay
178.128.93.56; a real SG node reserved on it, US/SG nodes got the nearest relay first).

**Pre-req — geo-controller on prod.** Prod runs the old controller (no geo). Build
`fleet-controller` (linux-amd64) from current main and deploy to the prod droplet
(581354476), verifying by sha256:
```
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -o /tmp/fc ./cmd/fleet-controller
scp /tmp/fc root@24.199.99.134:/root/fleet/fleet-controller.geo
# on the droplet: sha256 match → cp fleet-controller fleet-controller.bak; mv .geo fleet-controller; systemctl restart fleet-controller
```
(Backward-compatible: with 1 relay the ordering is a no-op.)

**Per relay (×4):**
```
doctl compute droplet create fleet-prod-relay-<region> --region <slug> \
  --size s-1vcpu-1gb --image ubuntu-24-04-x64 --ssh-keys 49536669 \
  --tag-name fleet-relay-prod --wait
# scp the fleet-relay linux binary → /root/fleet/fleet-relay
# systemd fleet-relay.service: ExecStart=/root/fleet/fleet-relay --port 4250 \
#   --announce <droplet-public-ip> --identity /root/fleet/relay.key --limit-mb 5120 --limit-min 120
# systemctl enable --now fleet-relay → grep the journal for /ip4/<ip>/udp/4250/quic-v1/p2p/<peerid>
```
DO droplets have no firewall by default → UDP 4250 is open.

**Wire into the prod controller `--relays`** (CSV of all relay multiaddrs, US first
for stability) in the main unit ExecStart, then `daemon-reload && restart`.

**Verify:** `/join` from IPs in each region returns the nearest relay first —
parse `relays[0]` from the JSON (⚠️ do NOT grep the raw response: `nats_url` also
contains the controller IP and sorts before `relays`).

## Deferred — China special handling (see the earlier discussion)

Not in this plan; documented so it's ready. China is a special case because the GFW
throttles **QUIC/UDP** (fleet is QUIC-only) and cross-border links are the wall.
Priority order if/when China matters:
1. **TCP transport** in the dataplane (fleet listens QUIC-only today; add a `/tcp/`
   listen addr — TCP/443 is far more GFW-resilient than QUIC/UDP). *Biggest lever.*
2. **lat/long ordering** (v2 of `relaygeo`): continent-level can't rank HK vs SG vs
   Bangalore (all "AS"), so a China node might not pick the nearest. Needed for #3 to
   matter.
3. **Hong Kong relay** (Vultr/Alibaba HK — DO has no HK; ~$6/mo): closest China-serving
   location without ICP. A mainland relay (Alibaba/Tencent) is optimal for domestic but
   needs an ICP filing + a Chinese entity.
Honest limit: China↔abroad transfers cross the GFW regardless of relays; domestic
China (HK/mainland relay + direct) works well.

## Housekeeping

The staging Singapore relay from the validation experiment is **still running**
(droplet `582744543` @ 178.128.93.56, $6/mo) — keep it (staging then has a real
2-region layout) or tear it down. Not part of this prod plan either way.

See also: `production.md` (D-full + prod controller runbook), the geo-ordering
commit `92018e3`, and the NAT/force-relay fix.
