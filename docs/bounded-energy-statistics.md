# Bounded energy statistics (opt-in, draft)

Draft/experimental. Tested against Home Assistant 2026.9.4 only; other versions are
not verified.

## What this does

An optional, off-by-default pipeline that records a verified interval-based
**external grid-import series** into Home Assistant long-term statistics, derived from
the raw interval CSV the integration already downloads. The existing sensor
presentation path is unchanged.

## Opt-in

* Enabled only by the literal boolean `True` at the config-entry option
  `enable_bounded_energy` (gate: `entry.options.get("enable_bounded_energy", False) is True`).
* No options-flow UI is added here; the option must be set directly. Any other value
  (absent, `false`, a string, `1`) leaves behaviour exactly as before.

## Source and data semantics

* Consumes the **original raw interval CSV text** from the existing single download
  (captured as immutable text); the legacy presentation object is derived from the same
  download, so the two never diverge.
* Uses only the current-account 30-minute **`Active Import Interval (kWh)`** rows.
  Recognised export and kW products are excluded — never converted, summed or added.
* Labels are naive `dd-mm-yyyy HH:MM` civil **interval-END** labels interpreted in
  Europe/Dublin. A label is usable only when exactly one UTC instant round-trips to the
  same civil wall clock **and** the same UTC offset.
* The 30-minute interval is subtracted in **UTC** after the unique instant is chosen,
  never in civil time.
* Energy is parsed as exact `Decimal` and rebuilt from integer micro-kWh output, so no
  ambient decimal precision/trap setting and no float intermediate can change it.

## Origin, excluded data and admission policy

* Fixed origin `2026-03-29T01:00:00Z`. Intervals before the origin are excluded for this
  stream; the origin is never rebased or auto-jumped.
* **Older data and fold-ambiguous / non-existent labels are excluded** (quarantined),
  never merged and never resolved by row order.
* Only the **safe contiguous complete prefix** is published, extending from the fixed
  origin toward the available input horizon. A trailing partial hour or interior gap
  prevents publication beyond that point. The reported verified watermark advances only
  after Recorder read-back confirms the submitted generation.
* **Corrections** to an already-known interval require an explicit conflict report before
  any merge. Conflicting duplicates for one unique interval are never merged — neither
  first- nor last-arrived wins.

## Publication

* Publishes one external statistics series (grid import) in kWh, mean type NONE, with a
  running sum.
* Each payload is a full-origin absolute replay of one approved ledger snapshot; the
  submitted prefix is immutable from the moment it is handed to the queue.
* Fail-closed identity pre-flight: an occupied statistic id without matching durable
  ownership provenance is refused rather than adopted or overwritten; no baseline is fabricated.
* The bounded read-back confirms **visibility only** — never queue drain. On timeout or
  error the publisher stops admissions for the process, leaves the ledger generation
  pending, and reports the last verified coverage. No retry storm and no queue polling
  loop.

## Failure preservation

* A failed, timed-out or superseded enqueue can never be replayed later with a different
  value: the prefix freezes at hand-off.
* Durable owner-record writes are atomic (private temp file, flush, fsync, replace,
  fsync directory, reopen-and-verify). Any write/fsync/replace/readback failure propagates
  as a durable-storage error and fences the publisher.

## Deliberately not claimed

* Not universal ESB source semantics or export support. This is a household-quality
  inference for the observed current-account product only, and must not be transferred to
  other accounts or products.
* No automatic energy selection and no verified daily authentication.
* No in-process historical correction/backfill reconciliation; that needs a separately
  authorised full-process boundary after the previous process has exited.
* This pipeline deliberately does not depend on the retained, unapproved prototype
  modules.
