# CO2 breathing detection: DEMO vs FIELD profiles

A trapped person breathes out CO2 and water vapour. The node's SCD41 sensor (on the SparkFun
SEN-25200) measures both, with a new reading every ~5 s. CO2 is **supporting evidence only**: on its
own it can make a node POSSIBLE, together with knocking or a voice it makes it LIKELY, and it can
never make it CONFIRMED (only a person pressing the button twice does that).

## Design: detect sudden increases, not "level above baseline"

Our first version flagged CO2 "X ppm above the baseline". In the demo room that failed in two
ways: CO2 drifts up slowly as people come and go (false alarms), and after a breath it stays
high for minutes (an alarm that won't clear). So CO2 now counts as evidence **only when it jumps
quickly**, as when someone is breathing close to the sensor, and that evidence lasts a fixed
**hold time**.

## How the node decides (`firmware/node/co2logic.py`)

- **Sudden rise:** `rise = CO2 now − the lowest CO2 in the last CO2_RATE_WINDOW seconds`. If
  `rise ≥ CO2_SUDDEN_PPM` on **2 readings in a row** (one noisy reading isn't enough), it's a
  sudden rise. It stays evidence for `CO2_EVENT_HOLD` seconds, and a rise that keeps going
  extends the hold.
- **Humidity:** if humidity also jumped by `RH_RISE_POINTS` over the same window, the level is
  BREATHING (exhaled breath is warm and wet). Otherwise it's SUDDEN_RISE.
- **Baseline:** the room's normal value, learned over `CO2_BASELINE_SECS` (median of the lower half of
  the readings, so a breath during learning doesn't inflate it). It then follows the room slowly,
  but is frozen during a rise. It is used **only for display and the ELEVATED label**, never as evidence.

| Level | Meaning | Changes status? |
|---|---|---|
| LEARNING | measuring the room after power-on | no |
| BREATHING | sudden rise held, humidity jumped too | POSSIBLE "breathing detected: CO2 +X ppm, humidity +Y%" |
| SUDDEN_RISE | sudden rise held, no humidity jump | POSSIBLE "sudden CO2 rise +X ppm in Ys" |
| SLOW_BUILDUP | FIELD only: steady climb of `CO2_TREND_PPM` over `CO2_TREND_MINUTES`, ≥ 70 % of steps up | POSSIBLE "slow CO2 build-up +X ppm over N min" |
| ELEVATED | well above the baseline but flat: room air | **no**, info only |
| NORMAL | everything else | no |

With knocking or a voice within `COMBINE_WINDOW` (120 s) the status becomes **LIKELY**
"breathing + knocking" / "breathing + voice". When the hold expires, the level drops to ELEVATED or
NORMAL even if CO2 is still high. That's on purpose: high but flat CO2 isn't evidence.

## The two profiles (`PROFILE` in `firmware/node/config.py`)

| Setting | DEMO | FIELD |
|---|---|---|
| Learn baseline | 30 s | 120 s |
| Sudden rise | +120 ppm within 15 s | +40 ppm within 60 s |
| Evidence hold | 60 s | 10 min |
| Humidity for BREATHING | +3 points | +3 points |
| Baseline follow | 0.02 per reading | 0.001 (nearly fixed) |
| Slow build-up rule | off | +60 ppm over 15 min |

**DEMO** is tuned for breathing straight onto the sensor at close range. We measured (see
`tests/RESULTS.md`) a room baseline of ~810 ppm, then **+150 ppm within 5 s** and **+380 ppm
within 10 s**, a peak of +460 ppm after ~30 s, and humidity going from 48 % to 54 %. +120 ppm on 2 readings
in a row gives BREATHING about **10 s** after someone starts breathing on the sensor. Slow room
drift (people arriving) only shows up as ELEVATED and never raises an alarm, and the evidence clears 60 s
after the breath, even though the room's CO2 takes minutes to come back down.

**FIELD** is for real rubble. There, a survivor's CO2 reaches the node diluted, so changes are
smaller and slower, and how big they are depends heavily on placing the node near a void
opening. FIELD therefore looks for a smaller rise over a longer window, holds the evidence much
longer, keeps the baseline nearly fixed, and adds the **slow build-up** rule, because a closed void
fills slowly.

## Limits and future work

- **Other CO2 sources:** generator exhaust, vehicles, and the rescuers' own breathing also raise
  CO2 and can cause sudden rises. Planned fix: a **reference node** placed away from the pile,
  whose readings are subtracted, so only CO2 coming out of the void counts.
- **Validation:** the FIELD numbers are educated guesses. They need to be validated at a US&R
  training site (voids with a volunteer inside, at several distances and airflows).
- The SEN55 on the same board is not started: its fan adds noise next to the sound sensor.
