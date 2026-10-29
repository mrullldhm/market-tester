# Cost rerun review — Codex, 2026-10-29

READ-ONLY review and independent arithmetic replication. No pytest run, commits, data edits, or other repository edits. Scratch scripts/results: `/tmp/codex-cost/`.

## Findings (most severe first)

Medium | `experiments/quantum/P10/run.py:165` | `--resume` disables the logged-item/missing-result check without verifying a batch decision or crash-resolution row; `--rerun` does not verify its claimed archive either. | An item has a `dev_start` but no JSON: `--resume` schedules it again without the manual resolution promised at lines 9–10/161. A missing entire output directory can similarly start another batch under `--resume`. New starts still count, so this does not erase trials or unlock holdout, but the decision safeguard is bypassed. | Require an existing batch ID bound to the runner/code and an explicit resolution for each unfinished start; verify and record archive identity for a new batch. The present rerun does have P10-001; P104-005 was explicitly voided by P104-006, so this is a latent runner flaw, not evidence that the current numbers are invalid.

Medium | `experiments/quantum/O10/run.py:151` | The override accepts any false `proceed`, although the logged justification specifically says MDE exceeds cost. | A matching-bundle power file with insufficient retained benchmark draws produces NaN SD/MDE and `proceed=False`; `--owner-override-power` would run anyway and record the inaccurate MDE justification. | Validate all six power rows, retained-sample validity, finite SD/MDE and counts before permitting an override of the numeric MDE threshold. The current six rows are finite; O10-006 records the actual STOP override. Missing/stale bundles still stop, all 204 configurations are logged before data loading, and candidate/holdout gates are not bypassed by this flag.

Low | `experiments/quantum/O10/run.py:189` | The `GridResult` still records baseline `slip_ticks=1`. | Its P&L matrix is now zero-slippage, but its metadata says one tick. Current `walk_forward` does not consume that field, so the reported arithmetic is unaffected. | Pass `config.SLIPPAGE_TICKS_PER_SIDE`.

Low | `experiments/quantum/T10/SPEC.md:7` | T10 still specifies $24.02; T10 lines 95/103/110 and `experiments/quantum/T20/SPEC.md:55`/65 still specify 2-tick stress despite their updated runners. | A literal spec replication uses different costs/gates from the current code. | Add a dated D005 supersession to both specs, preserving historical reports. P10/O10 already have such addenda; their older cost text is historical.

No other unintended numerical gate relaxation or active old baseline/stress default found in the reviewed Python/Pine diff. `prereg.check_slip` accepting finite numeric zero is appropriate under D005: `_is_number` still rejects booleans, NaN and infinities, negatives remain rejected, stress defaults remain one tick, and stricter-only gate overrides remain enforced. Frozen holdout code/hash and one-run protections are unchanged. An individual P102 PASS is not permission to register it contrary to the P10 batch rule.

## Independent replication

The supplied pytest log SHA-256 is exactly `8654a8ea59855a5ca9b2b0ecf65abb9fed5f91fedffaa0a147f83041f24d0392`; read log: **271 passed in 36.30s**. Did not rerun pytest.

A concurrent workspace addition, `strategies/quantum/h10.py`, changed the whole-source hash to `8f878f60e394…` during review. Excluding that new module reproduces the saved rerun hash exactly (`9d70ef73d7083a7f5ac782b40ac0d25caccbcb9c793c73eaae472f36a53194bf`). H10 and its new tests are outside this review and the supplied test-log evidence.

Own pandas/NumPy/SciPy arithmetic, with no `market_tester` imports:

- Recomputed gross as `side * (exit_ticks - entry_ticks) * $10`.
- New net = gross minus $4.02; old baseline/new stress = gross minus $4.02 minus `$10 * slip_sides`; old stress subtracts another `$10 * slip_sides`. Limit fills are unslipped. Thus the cost improvement is not universally $20/trade.
- All ten P10 trade lists (**50,978 trades**) have identical non-cost columns, including gross, timestamps, symbols, fills and exits. Daily trade counts are identical and daily net differences equal saved market-fill counts times $10. All ten new dev JSON summaries and 1-tick stress gates match independent arithmetic. New/archived SUMMARY values agree at printed precision.
- Raw-bar checks read only CL.v.0, 2010–23, directly from `data/processed/ohlcv-1m/year=YYYY/cl.parquet`. Across **66,539 distinct saved trade paths** from both batches: zero contract/contiguous-segment mismatches; all P10 entries match raw opens; signal/flat/roll/time exits match the applicable raw open/close. Six target fills outside the exit-bar range are conservative fills at the target after a favorable gap, not optimistic price improvement. This anchors saved prices/contracts; it does not independently reconstruct every strategy signal or the unsaved 204 O10 trade lists.

All amounts below are dollars per trade. **Codex equals Claude at displayed precision; difference $0.00 in every numeric table cell** (unrounded arithmetic tolerance $1e-9).

| Item | Trades | Gross, both | Old baseline | New baseline | New 1-tick stress |
|---|---:|---:|---:|---:|---:|
| P101 | 3,167 | 5.57 | -18.45 | 1.55 | -18.45 |
| P102 | 2,837 | 29.16 | 5.14 | 25.14 | 5.14 |
| P103 | 755 | 6.73 | -17.29 | 2.71 | -17.29 |
| P104 | 2,609 | 5.50 | -14.59 | 1.48 | -14.59 |
| P105 | 7,880 | -10.69 | -34.71 | -14.71 | -34.71 |
| P106 | 7,598 | 3.39 | -20.63 | -0.63 | -20.63 |
| P107 | 1,645 | -28.29 | -49.16 | -32.31 | -49.16 |
| P108 | 1,879 | 4.00 | -20.02 | -0.02 | -20.02 |
| P109 | 7,174 | 10.67 | -13.35 | 6.65 | -13.35 |
| P110 | 15,434 | 9.76 | -14.26 | 5.74 | -14.26 |

P104 improves by **$16.07512457/trade**, P107 by **$16.85106383**; other P10 items improve by exactly $20. P102 total net: old **$14,575.26**, new **$71,315.26**, increase **$56,740.00 = 2,837 × $20**. Its old 2-tick stress was **-$14.86243920/trade**, new 1-tick stress **+$5.13756080**, again +$20.

O10: all **204 matching configurations** retain identical trade counts, gross means and exit counts; baseline and stress costs reconcile throughout. New 1-tick stress equals old baseline for every matching configuration. Mean improvement ranges **$12.54471545–$17.98512748/trade**, reflecting unslipped limit fills.

| O10 fixed reference (TP30) | Trades | Gross, both | Old baseline / new stress | New baseline |
|---|---:|---:|---:|---:|
| S1, SL0 | 2,824 | -4.17 | -25.71 | -8.19 |
| S2, SL16 | 2,824 | -6.12 | -27.84 | -10.14 |
| S3, SL0 | 2,824 | 4.80 | -15.43 | 0.78 |
| S4, SL16 | 2,824 | 1.27 | -19.52 | -2.75 |
| S5, SL16 | 2,736 | -15.58 | -35.13 | -19.60 |
| S6, SL16 | 2,472 | -11.54 | -28.36 | -15.56 |

**O10 selected trade lists are not all unchanged.** Ten of thirteen saved old/new files have identical non-cost columns; S1-best, S3-best and pick-trades differ because the Sharpe ranking changes. S1 best TP34 → TP33; S3/overall pick TP35 → TP33. This follows the declared selection rule, rather than a changed fill algorithm. For the same new pick **S3/SL0/TP33** in both config tables: 2,824 trades, gross **$5.17705382**, old net/new stress **-$15.27354108**, new net **+$1.15705382**, difference **+$16.43059490/trade**. Comparing old selected TP35 net -$15.57099150 directly with new selected TP33 mixes selection and costs.

Independently recalculating yearly Sharpe selections and summing the saved daily matrices reproduces O10 walk-forward: old **2,228 trades, -$37,266.56, -$16.72646320/trade**; new **2,249 trades, +$2,039.02, +$0.90663406/trade**. Those selected paths therefore also cannot be described as a pure fixed-trade cost shift.

O10 POWER counts are unchanged: 2,824/2,824/2,824/2,824/2,736/2,472. Recomputing `MDE = (z(1-.05/204) + z(.8)) * saved_SD / sqrt(n)` matches both files. New MDEs: **$15.830373, $15.115348, $20.366641, $17.824037, $13.895572, $15.863716**. All exceed $4.02, correctly **STOP**; all old MDEs were below $24.02, correctly **PROCEED**. SDs change because market-fill counts vary across benchmark trades; removing nonconstant slippage need not preserve SD. Benchmark draws were not independently regenerated.

## P102 verdict

**P10 batch FAIL is correct. No candidate; no P102 holdout authorization.** `P10/SPEC.md:69` onward requires the conjunction of item PASS, both Holm families, batch DSR and concentration checks. Recomputing Holm gives profit **0.07496251874 > 0.05**, random **0.0405 <= 0.05**. Independent batch DSR, using the spec's **N = 10 + 1,056,017 = 1,056,027** and variance of the ten daily Sharpes, is **7.5690e-8** (prints 0.0000). The individual DSR **0.951536** counts only two noise_area runs; it does not replace the batch correction. PBO is inapplicable/null for the one-config item, not an additional measured success.

Reasons the attractive average is insufficient:

- 2019–23: **1,005 trades, +$2.79592040/trade, +$2,809.90 total**; 1-tick stress **-$17.20407960/trade**. That block can absorb only about **0.140 tick/side** additional average market slippage before reaching zero.
- 2020–21: **396 trades, -$27.98464646/trade, -$11,081.92 total**. 2010–18 supplies **$68,505.36**, approximately **96.1%** of full-sample net.
- Shorts supply **$66,916.98** versus longs **$4,398.28**; long-only mean is just **+$3.17336219** and fails stress. The ten best days contribute **$39,731.76**, about **55.7%** of net (concentration evidence, not a new post-hoc gate).
- Earlier blind replication remains **2,718 vs 2,837 trades**, a 119-trade unresolved rule-reading difference. The archive explicitly attributes it to same-direction exit/re-entry. Current saved data include 233 immediate same-direction exit/re-entry pairs; simply deleting 119 rows is not a valid reconciliation because subsequent position paths change. Repricing matching saved lists does not resolve this signal-level ambiguity.

Credibility requires a frozen, explicit priority for simultaneous same-side entry/exit; independent raw-bar reconciliation under that priority; no post-hoc choice between readings; continued accounting for related dead trials and cost/rule looks; and genuinely unseen forward evidence of an edge robust to execution costs and recent regimes. The related ORB family already has full-history/holdout looks in `LEDGER.csv` (TV10-001/002), so 2024+ cannot simply be relabelled pristine evidence for related research. These dev results warrant further skepticism, not weakening Holm/DSR or promoting the individual PASS.
