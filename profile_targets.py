"""Load numeric health targets from PROFILE.md when present.

The supported machine-readable format is a fenced yaml/yml block containing a
top-level `targets:` mapping. This intentionally supports only simple scalar
values so the project does not need a YAML dependency.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


DEFAULT_TARGETS = {
    "calories": 2300,
    "protein_g": 120,
    "fiber_g": 30,
    "sat_fat_g_limit": 22,
    "sugar_g_limit": 30,
    "sodium_mg_limit": 2300,
    "omega3_g": 2.0,
    "magnesium_mg": 420,
    "potassium_mg": 3400,
    "vitamin_d_mcg": 15,
    "iron_mg": 8,
    "b12_mcg": 2.4,
    "zinc_mg": 11,
    "vitamin_c_mg": 90,
    "vitamin_e_mg": 15,
    "vitamin_b6_mg": 1.3,
    "folate_mcg": 400,
    "protein_trigger_g": 27,
    "training_sessions_per_week": 3,
    "fasting_hours": 16,
    "steps": 8000,
    "active_calories": 400,
}


def _coerce_scalar(value: str):
    value = value.strip().strip('"').strip("'")
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _parse_targets_block(block: str) -> dict:
    targets = {}
    in_targets = False
    for raw in block.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if re.match(r"^targets\s*:\s*$", line):
            in_targets = True
            continue
        if not in_targets:
            continue
        if line and not line.startswith((" ", "\t")):
            break
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            targets[key] = _coerce_scalar(value)
    return targets


def load_profile_targets(profile_path: str | os.PathLike | None = None) -> dict:
    path = Path(profile_path or os.getenv("PROFILE_PATH", Path(__file__).parent / "PROFILE.md"))
    if not path.exists():
        return {}
    text = path.read_text()
    found = {}
    for match in re.finditer(r"```(?:ya?ml)\s*\n(.*?)```", text, flags=re.IGNORECASE | re.DOTALL):
        found.update(_parse_targets_block(match.group(1)))
    return found


def get_targets(profile_path: str | os.PathLike | None = None) -> dict:
    targets = DEFAULT_TARGETS.copy()
    targets.update(load_profile_targets(profile_path))
    return targets
