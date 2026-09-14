# Operating modes

Every tuning knob in the system is independently settable, which is powerful
and unhelpful. An operator commissioning a site does not want to reason about
`track1_conf_threshold` and `detection_stride_seconds` in isolation — they want
to say *"this is a bank vault"* or *"this is a car park with a small disk"*.

These five modes are coherent **combinations** of those knobs, each with a
stated purpose and a stated cost. They are deliberately opinionated: the point
is that the trade-off is made explicitly and written down, instead of emerging
by accident from whatever the thresholds were last left at.

Set from **Configuration → Modes**, or `POST /api/modes/{id}/apply`
(administrator only, audited). Editing any owned key afterwards puts the system
into **Custom**, which is reported honestly rather than pretending a mode is
still in force.

> **Modes affect future uploads only.** Already-processed segments keep the
> tier they were given. Re-run a project to re-tier it under a new mode.

---

## At a glance

| Mode | For | Worst-case critical miss | Relative storage | Relative compute |
|---|---|---|---|---|
| 🔒 **Forensic Hold** | Investigations, legal hold | ≈0% | ~0.85 | 2× |
| 🛡️ **High Security** | Banks, transport, custody | **1.27%** | **0.227** | 1× |
| ⚖️ **Balanced** | General purpose (default) | 6.43% | 0.269 | 1× |
| 💾 **Storage Economy** | Corridors, stockrooms, archive | elevated | ~0.10 | 0.5× |
| ⚡ **Edge / Low Power** | Constrained hardware, many streams | elevated for brief events | ~0.15 | 0.3× |

**Critical miss** = a segment with expert severity ≥ 0.80 that gets tiered LOW.
LOW keeps keyframes only, so the moving footage is destroyed — this is the
number that matters when the decision is irreversible. Figures come from
`training/calibrate_thresholds.py`, which sweeps boundary pairs across four
distributions (nominal, low-light, busy-scene, weak-models) and reports the
**worst case**, not the average.

---

## 🔒 Forensic Hold

> *Keep almost everything. Storage is the cheapest thing you own.*

**Purpose.** Active investigation, legal hold, or a site under a retention
order. Evidence must survive even if the scoring is wrong.

**Use when**
- An incident is under investigation and footage may be subpoenaed
- A new site whose normal behaviour the models have not yet seen
- Regulatory retention requirements override storage cost

**Cost.** Highest storage use by a wide margin — expect 3–5× the disk of
Balanced. Deletion is effectively disabled for a week (168-hour grace).

| Key | Value |
|---|---|
| `threshold_high` / `threshold_low` | 0.25 / 0.10 |
| `motion_sensitivity` | 0.0005 (very permissive) |
| `detection_stride_seconds` | 0.5 (2 fps sampling) |
| `tier3_grace_hours` | 168 (7 days) |

---

## 🛡️ High Security

> *Miss nothing that matters. Calibrated against model drift.*

**Purpose.** Sites where failing to keep footage of a real incident is the
dominant risk: banks, pharmacies, transport hubs, custody areas.

**Use when**
- A wrong LOW decision would destroy the only record of a crime
- Lighting or camera quality is variable, so model confidence drops
- You want the boundaries the calibration sweep actually recommends

**Cost.** Moderately more storage than Balanced, because ambiguous segments
land in MEDIUM (re-encoded video kept) rather than LOW (keyframes only).

**Why 0.90 / 0.30 and not 0.70 / 0.40.** This is the pair the sweep
recommends, and it is better on *both* axes — 1.27% worst-case critical miss
versus 6.43%, at storage index 0.227 versus 0.269. That is not a free lunch:
widening MEDIUM is simply cheaper than widening HIGH. Raising the HIGH boundary
moves borderline segments out of lossless storage, and lowering the LOW boundary
stops them falling into keyframe-only. The net is less disk *and* less
irreversible loss.

| Key | Value |
|---|---|
| `threshold_high` / `threshold_low` | 0.90 / 0.30 |
| `alert_threshold` | 0.75 |
| `track1_conf_threshold` | 0.30 |
| `tier3_grace_hours` | 72 (3 days) |

---

## ⚖️ Balanced

> *The default. Sensible trade-off for a typical site.*

**Purpose.** General-purpose surveillance where both storage and evidence
matter and neither dominates.

**Use when**
- Standard retail, office or residential monitoring
- No specific regulatory or incident-driven requirement
- As a starting point before tuning to a site's real footage

**Cost — stated plainly.** Balanced carries the known weakness the calibration
sweep found: under degraded model confidence, **6.4% of genuinely critical
segments are tiered LOW** and their video destroyed. If that matters at your
site, use High Security, which is both safer and cheaper.

| Key | Value |
|---|---|
| `threshold_high` / `threshold_low` | 0.70 / 0.40 |
| `motion_sensitivity` | 0.001 |
| `detection_stride_seconds` | 1.0 |
| `tier3_grace_hours` | 24 |

---

## 💾 Storage Economy

> *Maximum reduction. Accepts that some detail is lost.*

**Purpose.** Long-horizon archival on constrained storage, or low-risk areas
where the recording exists for completeness rather than evidence.

**Use when**
- Corridors, stockrooms, car parks — high volume, low incident rate
- Disk or cloud spend is the binding constraint
- Footage older than a few days is rarely retrieved

**Cost.** The highest rate of irreversible loss of the five. **Not appropriate
anywhere an incident would need to be reconstructed from video.** The 30-second
segments also make the timeline coarser, so an event is localised to a wider
window.

| Key | Value |
|---|---|
| `threshold_high` / `threshold_low` | 0.80 / 0.55 |
| `motion_sensitivity` | 0.004 (aggressive gate) |
| `detection_stride_seconds` | 2.0 |
| `segment_seconds` | 30 |
| `tier3_grace_hours` | 6 |

---

## ⚡ Edge / Low Power

> *Least compute. Leans hard on the motion gate.*

**Purpose.** Constrained hardware — an on-camera box, a shared VM, or a site
processing many streams on one machine.

**Use when**
- CPU, not disk, is the bottleneck
- Many cameras share one processing host
- Scenes are mostly static, so the motion gate can do the filtering

**Cost.** Coarser detector sampling means brief events can be missed entirely:
at a 3-second stride, **a weapon visible for under two seconds may never be
sampled**. If you are compute-bound, raise Stage 2 sensitivity before lowering
the detector rate — skipping a static segment is free, sampling a moving one
coarsely is not.

| Key | Value |
|---|---|
| `threshold_high` / `threshold_low` | 0.75 / 0.40 |
| `motion_sensitivity` | 0.006 (very aggressive gate) |
| `detection_stride_seconds` | 3.0 |
| `segment_seconds` | 30 |

---

## Custom

Any edit to an owned key puts the system into Custom. The Modes view shows
exactly which keys differ from each preset, so you can see how far a hand-tuned
setup has drifted — and adopt the nearest mode in one click if you want to
reset.

**Owned keys** (what a mode writes; everything else is left untouched):

```
threshold_high  threshold_low  alert_threshold  motion_sensitivity
track1_conf_threshold  track2_conf_threshold  detection_stride_seconds
action_min_frames  segment_seconds  tier3_grace_hours  context_rules
pipeline_profile
```

## Validating a mode at your site

A mode is a starting point, not a guarantee. To check one holds up on your own
footage:

```bash
python -m training.calibrate_thresholds --samples 12000
```

Then compare its recommendation against the mode you are running. If they
disagree materially, your site's footage differs from the corpus the scoring
model was calibrated on — which is exactly the situation the limitations
section in [VALIDATION.md](VALIDATION.md) warns about.


---

## Measured on a real corpus

Replaying every Ssig this system has produced (180 segments, 418 MB of source
footage) through each mode's boundaries — what `GET /api/modes/{id}/rationale`
and the **Why** button on each mode card show:

| Mode | HIGH / MEDIUM / LOW | Storage index | vs Balanced | Newly destroyed |
|---|---|---|---|---|
| 🔒 Forensic Hold | 125 / 43 / 10 | 0.746 | **+175%** | none |
| 🛡️ High Security | 1 / 118 / 59 | 0.128 | **−53%** | none |
| ⚖️ Balanced | 35 / 71 / 72 | 0.272 | — | none |
| 💾 Storage Economy | 2 / 60 / 116 | 0.077 | **−72%** | **44 segments / 128 MB** |
| ⚡ Edge / Low Power | 12 / 94 / 72 | 0.166 | −39% | none |

Two things worth reading twice:

**High Security is cheaper than Balanced *and* destroys nothing extra.** That
is the calibration sweep's prediction confirmed on real footage. Raising the
HIGH boundary pushes borderline segments out of lossless storage while lowering
the LOW boundary stops them falling into keyframe-only — most footage lands in
MEDIUM, which is both smaller than HIGH and recoverable, unlike LOW.

**Storage Economy is the only mode that destroys footage the current setup
keeps** — 44 segments, 128 MB. That is the trade-off it exists to make, and it
is the reason the mode card states it in red rather than advertising the 72%
saving alone.

*Figures move as the corpus changes; the Why panel always recomputes against
whatever the system has actually scored.*
