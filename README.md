# Recorder Downsampler

A Home Assistant integration that mirrors fast-updating sensors into **slow,
recorded siblings** — so you keep high-rate live display while cutting recorder
churn.

Sibling project to
[ha-recorder-tuning](https://github.com/bakerkj/ha-recorder-tuning): that one
controls _how long_ history is kept (per-entity purge); this one controls _how
often_ history is written.

## The problem

Some integrations push values every few seconds (e.g. whole-home energy monitors
reporting per-channel power every ~15 s, or Wi-Fi signal-strength sensors every
~10 s, …). The frontend only needs that speed _live_ — the recorder does not
need a row every 15 s. But you can't just exclude the source from the recorder,
because **excluding an entity also stops its long-term statistics** (statistics
are compiled from recorded states).

## How it works

For each matched source, this integration creates one mirror sensor
(`sensor.<source>_downsampled`) that:

- subscribes to the source **live** and buffers its values each interval;
- on a fixed **interval** (default 1 min) writes a single **aggregated** value
  (mean / max / min / last / …), so the recorder sees one row per interval —
  numeric sources are aggregated by `method`; non-numeric (string) sources are
  sampled with `first`/`last` (any other method falls back to `last`);
- copies the source's `unit`, `device_class`, `state_class`, and **attaches to
  the source's device** so it shows up on the same device card;
- being a normal recorded sensor, generates its own 5-min / hourly statistics.

You keep the **source** for live 15 s display (recorder-exclude it so it stops
churning); the **mirror** carries the downsampled history.

```yaml
# configuration.yaml
recorder_downsampler:
  interval: "00:01:00" # global default cadence
  method: auto # global default aggregation
  precision: auto # global default decimal places for the recorded value
  warn_unexcluded: true # warn if a mirrored source is still recording
  rules: !include recorder_downsampler.yaml

recorder:
  exclude:
    entity_globs:
      - sensor.*_power # the sources the rule below mirrors
```

```yaml
# recorder_downsampler.yaml
- name: Fast power sensors
  entity_regex_include: ["_power$"]
  interval: "00:01:00"
  method: auto # measurement -> mean
```

## Installation

### HACS (recommended)

Add this repository as a custom HACS integration repository, then install
**Recorder Downsampler**.

### Manual

Copy `custom_components/recorder_downsampler/` into your
`config/custom_components/` directory and restart Home Assistant.

## Selecting sources

Rules use the same vocabulary as ha-recorder-tuning:

| key                    | meaning                                        |
| ---------------------- | ---------------------------------------------- |
| `integration_filter`   | match by integration/platform (registry)       |
| `device_ids`           | match by device (registry)                     |
| `entity_ids`           | explicit entity ids                            |
| `entity_globs`         | fnmatch globs                                  |
| `entity_regex_include` | regex; entity must match at least one          |
| `entity_regex_exclude` | regex; entity removed if it matches any        |
| `match_mode`           | `all` (intersection, default) or `any` (union) |
| `interval`             | per-rule override of the cadence               |
| `method`               | per-rule override of the aggregation           |
| `precision`            | per-rule override of the recorded precision    |
| `backfill_history`     | per-rule auto-backfill (overrides the global)  |
| `enabled`              | set `false` to stage a rule out                |

## Aggregation (`method`)

`auto` (default) picks by the source's `state_class`:

- `measurement` → **mean** (energy-consistent for power)
- `total` / `total_increasing` → **last** (preserves the cumulative counter)
- otherwise → **last**

Override per rule with `mean`, `median`, `max`, `min`, `last`, `first`, or
`circular_mean` (vector mean for angular sources like wind direction — 350° and
10° average to 0°, not 180°; samples are treated as degrees and the result is in
`[0, 360)`. If the samples cancel out, the interval is skipped).

## Precision (`precision`)

Rounds the value the recorder actually stores (not just its display). `auto`
(default) is type-aware:

- `total` / `total_increasing` (cumulative counters) → **never rounded**, so the
  Energy dashboard's `sum` statistics and the counter stay exact;
- everything else → rounded to the source's `suggested_display_precision` if it
  declares one, otherwise left **raw** (precision is never invented).

Override with an integer (decimal places, e.g. `precision: 1`; `0` rounds to a
whole number) or `none` to force raw. Cumulative counters stay protected even
when you set an explicit integer — rounding a kWh accumulator would break the
Energy dashboard. Non-numeric (string) sources are never rounded.

## Important caveats

- **You must still recorder-exclude the sources.** An integration cannot change
  the recorder's include/exclude filter (that's built once at recorder setup).
  With `warn_unexcluded: true`, this integration logs a warning naming any
  mirrored source that is still being recorded.
- **Energy dashboard:** it runs on the _energy_ (kWh) sensors' `sum` statistics,
  not the power (W) sensors. Downsampling **power** is safe. Downsampling an
  energy sensor that the Energy dashboard references means re-pointing the
  dashboard at the mirror — generally leave energy accumulators recorded.
- **Reload** adds newly matched mirrors and retunes existing mirrors whose
  `interval` / `method` / `precision` changed — all live, no restart needed. A
  mirror whose source a reload no longer matches is **never auto-deleted** (it
  may carry history) — it's surfaced as a **Repair** for you to delete or keep.
  (A rule explicitly flipped to `dry_run` is the one exception: its live mirror
  is torn down, since that's a deliberate "simulate only" directive.)
- **Source outages** are reflected: if the source is unavailable/unknown for a
  whole interval the mirror goes `unavailable`; if the source simply didn't
  update, the mirror holds its last value (no new recorder row). When the source
  returns, the mirror recovers on the next interval — even if it missed the
  source's recovery event — so a dropped subscription can't strand it.
- **Mirrors are never auto-deleted.** A leftover mirror whose source is gone,
  disabled, or no longer matched is raised as a **Repair** (delete / ignore,
  each / all); a source that simply hasn't loaded yet is left untouched. Orphan
  reconciliation also waits until Home Assistant has fully started, so a slow
  source integration is never mistaken for a removed one.

## Backfilling history (experimental)

A mirror only has history from when it was created, so re-pointing the Energy
dashboard (or a statistics graph) at it shows a gap before that. The
**`recorder_downsampler.backfill_history`** service grafts each mirror's
pre-creation long-term statistics from its source, so the re-pointed series
stays continuous:

- **cumulative** (energy / `sum`) history is shifted so it meets the mirror's
  baseline at the cutover — historical `sum` values therefore run **negative**
  before the cutover. That's intentional and correct: the Energy dashboard reads
  period _deltas_, which stay positive and monotonic across the join.
- **mean** (power / measurement) history is copied verbatim.
- non-numeric sources have no statistics and are skipped.

It's **idempotent** — re-running won't copy anything twice (already-backfilled
mirrors are skipped). Set `entity_id:` to target specific mirrors, or omit it
for all; pass `dry_run: true` to preview how many rows each mirror would gain
without writing anything. To do it automatically, set `backfill_history: true` —
either globally (every new mirror) or on a single rule, where it overrides the
global for just that rule's mirrors. It defaults to `false` and runs once per
mirror in the background — at startup **or on reload**, so flipping it on for a
rule and reloading grafts that rule's mirrors without a restart. Idempotent, so
a routine reload with nothing new is a no-op. Given how risky rewriting
statistics is, you can scope auto-backfill to one rule, verify the graft, then
widen it.

On completion (real runs, not `dry_run`) it logs each grafted mirror plus a
summary, fires a **`recorder_downsampler_backfill_completed`** event (data:
`grafted` / `rows` / `skipped` / `failed` / `duration_s` — automate off it), and
raises a **persistent notification** when it grafts anything or any mirror
fails. The last run's counts are persisted, so there's a durable record it ran.

> [!WARNING] **Experimental, and it rewrites long-term statistics.** Take a
> database backup first, and validate on one mirror before running it
> fleet-wide. It depends on a recorder internal (the forward `sum` is seeded
> from a sensor's latest _short-term_ statistics, so grafting only older hourly
> rows is safe) — a guard test pins that assumption. If a graft goes wrong, see
> "Recovering from a backfill" below.

### Recovering from a backfill

A backfill only **adds** historical rows older than the mirror's first; it never
touches the mirror's own ongoing statistics. So if a graft looks wrong (wrong
source, an off shift), recovery is straightforward and your live recording is
unaffected:

1. **Delete the mirror's statistics** — Developer Tools → Statistics → find the
   mirror → delete. This drops the grafted history (and the mirror's own
   long-term stats, which it then rebuilds from its continuing recording).
2. **Clear its backfilled flag** so it can be grafted again — stop HA, remove
   the mirror's id from the `"backfilled"` list in
   `/config/.storage/recorder_downsampler.state` (or delete that file, which
   also resets orphan-ignore choices), then start HA.
3. **Re-run** `backfill_history` (with `dry_run: true` first) once the source /
   shift is right.

Restoring from the database backup you took beforehand is always the safe
fallback.

## Development

```bash
make test              # fast unit tests
make test-integration  # real-HA integration tests
uvx prek run --all-files
```

Install the git hooks (including the commit-msg hook for commitlint) with
`uvx prek install --overwrite --hook-type pre-commit --hook-type commit-msg`.

Tooling mirrors ha-recorder-tuning: `uv` + Python 3.14, ruff/mypy/codespell/
prettier/actionlint prek hooks, conventional commits, release-please, HACS zip
release, and a weekly HA-`dev` signature-compat guard.
