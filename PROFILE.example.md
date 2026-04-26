# User Profile

> Copy this file to `PROFILE.md` and fill in your details. `PROFILE.md` is gitignored — it stays on your machine. The agent reads it to tailor analysis to you specifically.

## Demographics
- Sex:
- Date of birth:
- Height (cm):
- Weight (kg):

## Lifestyle
- Occupation / activity baseline (e.g. desk job, on feet all day):
- Sleep schedule (typical bedtime / wake time):

## Goals
List in priority order. Examples: longevity, strength, lean body composition, energy, athletic performance, weight loss, cognitive performance.
- 1.
- 2.
- 3.

## Training
- Style (e.g. kettlebells, calisthenics, gym, running, mixed):
- Frequency (sessions/week):
- Equipment / location:

## Diet
- Pattern (e.g. Mediterranean, high-protein, vegetarian, omnivore):
- Eating window (e.g. 8h TRE, 3 meals + snacks):
- Foods you eat regularly (helps the agent score recurring meals faster):

## Substances
Track honestly — the agent uses this to correlate with HRV/REM/recovery, not to judge.
- Cannabis (frequency, typical dose):
- Alcohol (frequency, typical drinks):
- Caffeine (frequency, typical mg):
- Nicotine (frequency):
- Other (medications, supplements):

## Relevant medical / physiological notes
- Conditions, allergies, injuries, family history:
- Anything that should override default reference ranges:

## Custom targets
If you want to override the default daily nutrient and leveling targets, edit the fenced block below. Code reads this block with a tiny custom parser, not a full YAML library: keep it to one flat `targets:` mapping with simple `key: value` scalar pairs. Do not use nested mappings, lists, or complex quoting.

```yaml
targets:
  calories: 2300
  protein_g: 120
  fiber_g: 30
  sat_fat_g_limit: 22
  sugar_g_limit: 30
  sodium_mg_limit: 2300
  omega3_g: 2.0
  magnesium_mg: 420
  potassium_mg: 3400
  vitamin_d_mcg: 15
  iron_mg: 8
  b12_mcg: 2.4
  zinc_mg: 11
  vitamin_c_mg: 90
  vitamin_e_mg: 15
  vitamin_b6_mg: 1.3
  folate_mcg: 400
  protein_trigger_g: 27
  training_sessions_per_week: 3
  fasting_hours: 16
  steps: 8000
  active_calories: 400
```
