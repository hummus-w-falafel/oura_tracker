# Solo Leveling — Health RPG System

## Concept
A gamified leveling system built on real biometric and behavioral data. Unlike traditional games, levels can regress if health regresses. Every stat is backed by measurable data — no vanity metrics.

The system is designed to be extensible. Health is the first domain; future domains can be added as new stat categories without changing the core XP/level engine.

---

## Stats (each 0-100)

Stats use the window that fits the signal: some components are today's Oura score, some are 7-day rolling averages, and some compare recent values against a longer baseline. Numeric targets such as steps, active calories, protein, training sessions per week, and fasting hours come from the fenced `targets:` block in `PROFILE.md` when present, falling back to the defaults shown here.

### VIT (Vitality) — How well is your body recovering?
| Component | Weight | Source |
|-----------|--------|--------|
| Sleep score | 30% | Today's Oura `daily_sleep.score` |
| Readiness score | 30% | Today's Oura `daily_readiness.score` |
| HRV vs personal baseline | 25% | `sleep_periods.average_hrv` 7-day avg vs prior 30-day baseline |
| Resting HR vs baseline | 15% | `sleep_periods.lowest_heart_rate` 7-day avg vs prior 30-day baseline (lower = better) |

If there is not enough baseline history yet, HRV and resting HR fall back to raw-value scoring.

### STR (Strength) — Are you getting stronger?
| Component | Weight | Source |
|-----------|--------|--------|
| Progressive overload | 35% | Best `reps * weight_lbs * (1 + weight_per_hand)` per exercise over recent 14 days vs the prior window |
| Training volume | 35% | Hard sets in the last 7 days (20+ sets = 100) |
| Post-workout recovery | 30% | Next-day readiness score after training days in the last 7 days |

**Data source:** `workout_sets` table (exercise, set_number, reps, weight_lbs, weight_per_hand).

The user's current routine, exercise list, and weekly session target live in `PROFILE.md`.
STR is unavailable until at least one recent `workout_sets` entry exists. When overload history is too sparse, the overload component defaults to neutral 50.

### END (Endurance) — How active and cardiovascularly fit are you?
| Component | Weight | Source |
|-----------|--------|--------|
| Daily steps | 30% | 7-day avg of `daily_activity.steps`, scaled to `targets.steps` (default 8000) |
| Active calories | 20% | 7-day avg of `daily_activity.active_calories`, scaled to `targets.active_calories` (default 400) |
| Resting HR trend | 25% | 7-day avg of `sleep_periods.lowest_heart_rate` vs prior 90-day baseline (lower = better) |
| VO2 max | 25% | Most recent `vo2_max` value on or before the day |

If there is not enough resting-HR baseline history, resting HR falls back to raw-value scoring. If VO2 max is missing, that component is omitted and weights redistribute.

### NUT (Nutrition) — Are you eating well?
| Component | Weight | Source |
|-----------|--------|--------|
| Nutrition score | 70% non-training / 50% training | Today's `compute_nutrition_score()` output (0-100, already accounts for all macros + micros) |
| Protein distribution | 30% | Today's MPS triggers + total protein adequacy. Defaults: 2+ meals at 27g+ and 120g total = 100. Override with `targets.protein_trigger_g` and `targets.protein_g`. |
| Calorie adequacy | 20% training only | On kettlebell/rowing days, today's calories vs `targets.calories`; no penalty above target |

Training days are detected from `workouts.activity IN ('kettlebell', 'rowing')`. Non-training days use only nutrition score and protein distribution.

### DIS (Discipline) — Are you showing up consistently?
| Component | Weight | Source |
|-----------|--------|--------|
| Bedtime consistency | 40% | Stddev of `sleep_periods.bedtime_start` hour over the last 7 days (lower = higher score) |
| Training adherence | 30% | Sessions completed / `targets.training_sessions_per_week` (default 3) |
| Fasting window adherence | 30% | Avg fasting window over logged meal days in the last 7 days vs `targets.fasting_hours` (default 16h) |

Fasting windows are computed from first/last meal timestamps on days with at least two logged meals.

---

## XP System

### Daily XP earned
Each stat generates `stat_score / 5` XP per day (0-20 XP per stat).

With 5 stats: **max 100 XP/day**.

When new stats are added, max daily XP scales proportionally.

### Daily decay
Decay is 50% of the maximum XP available from stats that have data that day. With all 5 stats active, this is **50 XP/day**. This means:
- Average performance (about 50% of available XP) = maintain level
- Great day (well above decay) = climbing
- Bad day (well below decay) = regressing
- Complete skip (0 XP) = loses the full active decay amount

Decay adjusts proportionally when stats are missing or when new stats are added.

### Level curve (exponential)
| Level | Total XP | Rough timeline |
|-------|----------|----------------|
| 1 | 0 | Day 1 |
| 5 | 500 | ~2 weeks solid |
| 10 | 2,000 | ~2 months consistent |
| 15 | 4,500 | ~4 months |
| 20 | 8,000 | ~6 months sustained |
| 30 | 18,000 | ~1 year |
| 50 | 50,000 | Long-term commitment |

Formula: `XP_required(level) = 20 * level^2`

### Regression
Level drops naturally when XP decays below the threshold. No artificial protection — if you stop performing, you feel it.

---

## Rank System
Layered on top of levels. Each rank requires a minimum level and minimum threshold across active stats to prevent min-maxing.

| Rank | Minimum level | Min active stat requirement |
|------|------------|---------------------|
| E | 1 | None |
| D | 6 | 30 |
| C | 11 | 45 |
| B | 21 | 55 |
| A | 31 | 65 |
| S | 41 | 75 |

---

## Display
Dedicated page at `/status` (`templates/status.html`). Features:
- Level, rank, XP progress bar (with today's XP contribution highlighted)
- 5 stat bars (VIT/STR/END/NUT/DIS) — click to expand component breakdown
- Each component shows score bar, weight %, and raw data explanation
- Click a component to toggle a 7-day sparkline chart of that component's score
- XP summary: today's earned (green), decay (red), net (green/red), total
- 7-day history bar chart with tooltip showing per-stat breakdown

---

## Database

### `workout_sets` table
`id PK AUTOINCREMENT | workout_day TEXT | exercise TEXT | set_number INTEGER | reps INTEGER | weight_lbs REAL | weight_per_hand INTEGER (0=single, 1=each hand) | notes TEXT | created_at TEXT | workout_id TEXT FK→workouts(id)`

Volume scoring uses total hard sets per week (not poundage). The current implementation scales 20+ sets in the last 7 days to 100.

## Data gaps / bootstrapping
- **END VO2 max** is omitted until Oura provides a value; weights redistribute over available components.
- Stats with missing components are scored on available components only (weights redistribute)
- First 7 days: stats ramp up as the rolling window fills, everyone starts at Level 1

---

## Extensibility
Adding a new stat:
1. Define components, weights, and data sources
2. Add computation to the stats engine
3. Max daily XP and decay auto-adjust (always 20 XP per stat, decay = 50% of max)
4. Level curve stays the same — more stats means faster potential leveling but also more to maintain

Future domains (non-health):
- Same XP/level engine, new stat categories
- Could have separate domain levels or one unified level
- Design decision deferred until health system is proven
