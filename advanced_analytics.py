"""Advanced time-series analytics for wearable and manual health data."""

from __future__ import annotations

from collections import defaultdict
from datetime import date as Date, datetime, time, timedelta, timezone
import math
import random
from statistics import median
import warnings
from zoneinfo import ZoneInfo

import numpy as np


METHOD_VERSION = "1"
SLEEP_TYPES = {"long_sleep", "sleep", "late_nap"}


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _percentile(values, percentile):
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=float), percentile))


def _robust_scale(values):
    arr = np.asarray(values, dtype=float)
    center = float(np.median(arr))
    mad = float(np.median(np.abs(arr - center)))
    scale = 1.4826 * mad
    if scale <= 1e-9:
        scale = float(np.std(arr)) or 1.0
    return (arr - center) / scale, center, scale


def _daily_segments(dates, values):
    """Interpolate one-day gaps and split longer gaps into contiguous segments."""
    points = {
        Date.fromisoformat(str(day)): float(value)
        for day, value in zip(dates, values)
        if value is not None and math.isfinite(float(value))
    }
    ordered = sorted(points)
    for left, right in zip(ordered, ordered[1:]):
        if (right - left).days == 2:
            missing = left + timedelta(days=1)
            points[missing] = (points[left] + points[right]) / 2

    segments = []
    current = []
    for day in sorted(points):
        if current and (day - current[-1][0]).days != 1:
            segments.append(current)
            current = []
        current.append((day, points[day]))
    if current:
        segments.append(current)
    return segments


def detect_change_points(
    dates,
    values,
    *,
    min_obs=42,
    min_size=14,
    penalty_scale=3.0,
    higher_is_better=None,
):
    """Detect stable baseline shifts with PELT and robust effect summaries."""
    import ruptures as rpt

    valid_count = sum(value is not None for value in values)
    result = {
        "method": "PELT l2",
        "method_version": METHOD_VERSION,
        "status": "ready",
        "observations": valid_count,
        "minimum_observations": min_obs,
        "changes": [],
        "series": [
            {"day": str(day), "value": round(float(value), 4)}
            for day, value in zip(dates, values)
            if value is not None and math.isfinite(float(value))
        ],
    }
    eligible = [segment for segment in _daily_segments(dates, values) if len(segment) >= min_obs]
    if not eligible:
        result["status"] = "collecting"
        result["reason"] = "No contiguous series is long enough for two stable regimes."
        return result

    changes = []
    for segment in eligible:
        seg_dates = [item[0] for item in segment]
        raw = np.asarray([item[1] for item in segment], dtype=float)
        scaled, _, full_scale = _robust_scale(raw)
        base_penalty = penalty_scale * math.log(len(raw))
        candidates = []
        for factor in (0.85, 1.0, 1.15):
            breaks = rpt.Pelt(model="l2", min_size=min_size, jump=1).fit(scaled).predict(
                pen=base_penalty * factor
            )
            candidates.append([point for point in breaks if point < len(raw)])

        stable = [
            point
            for point in candidates[1]
            if all(any(abs(point - other) <= 2 for other in group) for group in candidates)
        ]
        for point in stable:
            if point < min_size or len(raw) - point < min_size:
                continue
            before = raw[max(0, point - min_size):point]
            after = raw[point:point + min_size]
            before_med = float(np.median(before))
            after_med = float(np.median(after))
            delta = after_med - before_med
            robust_effect = delta / full_scale if full_scale else 0.0
            if abs(robust_effect) < 0.35:
                continue
            pct = delta * 100 / abs(before_med) if abs(before_med) > 1e-9 else None
            favorable = None
            if higher_is_better is not None:
                favorable = delta > 0 if higher_is_better else delta < 0
            changes.append({
                "day": seg_dates[point].isoformat(),
                "before": round(before_med, 3),
                "after": round(after_med, 3),
                "delta": round(delta, 3),
                "percent_change": round(pct, 1) if pct is not None else None,
                "robust_effect": round(robust_effect, 2),
                "magnitude": "strong" if abs(robust_effect) >= 1 else "moderate",
                "direction": "higher" if delta > 0 else "lower",
                "favorable": favorable,
            })

    result["changes"] = sorted(changes, key=lambda item: item["day"])
    return result


def _analysis_day_and_slot(local_dt):
    analysis_day = local_dt.date() if local_dt.time() >= time(12) else local_dt.date() - timedelta(days=1)
    noon = datetime.combine(analysis_day, time(12), tzinfo=local_dt.tzinfo)
    minutes = int((local_dt - noon).total_seconds() // 60) % 1440
    return analysis_day, min(287, minutes // 5)


def compute_sleep_regularity(periods, *, tz_name="America/Toronto", max_days=90):
    """Compute the Phillips Sleep Regularity Index from five-minute stages."""
    tz = ZoneInfo(tz_name)
    day_states = {}
    main_sleep_states = {}
    valid_days = set()

    for period in periods:
        if period.get("type") not in SLEEP_TYPES or not period.get("bedtime_start"):
            continue
        phases = period.get("sleep_phase_5_min") or ""
        if not phases:
            continue
        start_utc = _parse_dt(period["bedtime_start"]).astimezone(timezone.utc)
        local_start = start_utc.astimezone(tz)
        period_day, _ = _analysis_day_and_slot(local_start)
        if period.get("type") == "long_sleep":
            valid_days.add(period_day)
            main_sleep_states.setdefault(period_day, [0] * 288)
        for index, phase in enumerate(phases):
            local_dt = (start_utc + timedelta(minutes=5 * index)).astimezone(tz)
            analysis_day, slot = _analysis_day_and_slot(local_dt)
            state = day_states.setdefault(analysis_day, [0] * 288)
            if phase in {"1", "2", "3"}:
                state[slot] = 1
                if period.get("type") == "long_sleep":
                    main_sleep_states.setdefault(analysis_day, [0] * 288)[slot] = 1

    ordered = sorted(day for day in valid_days if day in day_states)[-max_days:]
    pairs = []
    for previous, current in zip(ordered, ordered[1:]):
        if (current - previous).days != 1:
            continue
        matches = sum(a == b for a, b in zip(day_states[previous], day_states[current]))
        pair_sri = -100 + 200 * matches / 288
        pairs.append({"day": current.isoformat(), "sri": round(pair_sri, 1), "matches": matches})

    def rolling(index, window):
        start = max(0, index - window + 1)
        values = [item["sri"] for item in pairs[start:index + 1]]
        return round(sum(values) / len(values), 1) if values else None

    trend = []
    for index, item in enumerate(pairs):
        trend.append({
            "day": item["day"],
            "pair_sri": item["sri"],
            "sri_7d": rolling(index, 7),
            "sri_14d": rolling(index, 14),
            "sri_30d": rolling(index, 30),
        })

    overall = round(sum(item["sri"] for item in pairs) / len(pairs), 1) if pairs else None
    return {
        "method": "Phillips SRI, 5-minute noon-to-noon epochs",
        "method_version": METHOD_VERSION,
        "status": "ready" if len(pairs) >= 4 else "collecting",
        "sri": overall,
        "valid_days": len(ordered),
        "valid_day_pairs": len(pairs),
        "epoch_pairs": len(pairs) * 288,
        "raster_scope": "long_sleep",
        "trend": trend,
        "raster": [
            {
                "day": day.isoformat(),
                "states": "".join(str(value) for value in main_sleep_states.get(day, [0] * 288)),
            }
            for day in ordered
        ],
    }


def _cosinor_from_bins(bin_values):
    bins = sorted(index for index, values in bin_values.items() if values)
    if len(bins) < 3:
        return None
    hours = np.asarray([index / 4 for index in bins], dtype=float)
    observed = np.asarray([median(bin_values[index]) for index in bins], dtype=float)
    omega = 2 * np.pi / 24
    design = np.column_stack([np.ones(len(hours)), np.cos(omega * hours), np.sin(omega * hours)])
    coefficients, _, _, _ = np.linalg.lstsq(design, observed, rcond=None)
    fitted = design @ coefficients
    residual = float(np.sum((observed - fitted) ** 2))
    total = float(np.sum((observed - np.mean(observed)) ** 2))
    mesor, beta_cos, beta_sin = [float(value) for value in coefficients]
    amplitude = math.hypot(beta_cos, beta_sin)
    peak = (math.atan2(beta_sin, beta_cos) / omega) % 24
    return {
        "mesor": mesor,
        "amplitude": amplitude,
        "peak_hour": peak,
        "nadir_hour": (peak + 12) % 24,
        "r2": 1 - residual / total if total > 0 else 0.0,
        "bins": bins,
    }


def fit_cosinor(readings, *, tz_name="America/Toronto", bootstrap_samples=200):
    """Fit a 24-hour cosinor to equal-weighted day/clock-bin HR medians."""
    tz = ZoneInfo(tz_name)
    daily_bins = defaultdict(lambda: defaultdict(list))
    for reading in readings:
        if reading.get("bpm") is None or reading.get("source") in {"workout", "live"}:
            continue
        local_dt = _parse_dt(reading["timestamp"]).astimezone(tz)
        bin_index = local_dt.hour * 4 + local_dt.minute // 15
        daily_bins[local_dt.date()][bin_index].append(float(reading["bpm"]))

    day_medians = {
        day: {index: median(values) for index, values in bins.items()}
        for day, bins in daily_bins.items()
    }
    aggregate = defaultdict(list)
    for bins in day_medians.values():
        for index, value in bins.items():
            aggregate[index].append(value)

    base = _cosinor_from_bins(aggregate)
    status = "ready" if len(day_medians) >= 14 and base and len(base["bins"]) >= 48 else "collecting"
    result = {
        "method": "24-hour cosinor, 15-minute non-workout HR medians",
        "method_version": METHOD_VERSION,
        "status": status,
        "days": len(day_medians),
        "covered_bins": len(base["bins"]) if base else 0,
        "minimum_days": 14,
    }
    if status != "ready":
        result["reason"] = "At least 14 days and 12 hours of clock-bin coverage are required."
        return result

    omega = 2 * np.pi / 24
    result.update({
        "mesor": round(base["mesor"], 2),
        "amplitude": round(base["amplitude"], 2),
        "peak_hour": round(base["peak_hour"], 2),
        "nadir_hour": round(base["nadir_hour"], 2),
        "r2": round(base["r2"], 3),
        "curve": [],
    })
    for index in range(96):
        hour = index / 4
        observed_values = aggregate.get(index, [])
        fitted = base["mesor"] + base["amplitude"] * math.cos(omega * (hour - base["peak_hour"]))
        result["curve"].append({
            "hour": hour,
            "observed": round(median(observed_values), 2) if observed_values else None,
            "fitted": round(fitted, 2),
        })

    if bootstrap_samples:
        rng = random.Random(20260824)
        days = list(day_medians)
        boot = []
        for _ in range(bootstrap_samples):
            sampled = [days[rng.randrange(len(days))] for _ in days]
            bins = defaultdict(list)
            for day in sampled:
                for index, value in day_medians[day].items():
                    bins[index].append(value)
            fitted = _cosinor_from_bins(bins)
            if fitted:
                boot.append(fitted)
        if boot:
            peak_diffs = [((item["peak_hour"] - base["peak_hour"] + 12) % 24) - 12 for item in boot]
            result["confidence_95"] = {
                "mesor": [round(_percentile([item["mesor"] for item in boot], 2.5), 2),
                          round(_percentile([item["mesor"] for item in boot], 97.5), 2)],
                "amplitude": [round(_percentile([item["amplitude"] for item in boot], 2.5), 2),
                              round(_percentile([item["amplitude"] for item in boot], 97.5), 2)],
                "peak_hour": [round((base["peak_hour"] + _percentile(peak_diffs, 2.5)) % 24, 2),
                              round((base["peak_hour"] + _percentile(peak_diffs, 97.5)) % 24, 2)],
            }
    return result


def cosinor_trend(readings, *, tz_name="America/Toronto", window_days=30, step_days=7):
    """Return rolling cosinor summaries without bootstrap confidence intervals."""
    tz = ZoneInfo(tz_name)
    dated = []
    for reading in readings:
        try:
            local_day = _parse_dt(reading["timestamp"]).astimezone(tz).date()
        except (KeyError, TypeError, ValueError):
            continue
        dated.append((local_day, reading))
    if not dated:
        return []
    first = min(day for day, _ in dated)
    end = max(day for day, _ in dated)
    cursor = first + timedelta(days=window_days - 1)
    trend = []
    while cursor <= end:
        start = cursor - timedelta(days=window_days - 1)
        window = [reading for day, reading in dated if start <= day <= cursor]
        fitted = fit_cosinor(window, tz_name=tz_name, bootstrap_samples=0)
        if fitted.get("status") == "ready":
            trend.append({
                "day": cursor.isoformat(),
                "mesor": fitted["mesor"],
                "amplitude": fitted["amplitude"],
                "peak_hour": fitted["peak_hour"],
                "r2": fitted["r2"],
            })
        cursor += timedelta(days=step_days)
    return trend


VAR_MODELS = {
    "recovery": {
        "label": "Recovery System",
        "variables": ["readiness_score", "sleep_score", "hrv", "lowest_hr"],
        "tests": [
            (predictor, outcome)
            for predictor in ("sleep_score", "hrv", "lowest_hr")
            for outcome in ("readiness_score", "sleep_score", "hrv", "lowest_hr")
            if predictor != outcome
        ],
    },
    "activity": {
        "label": "Activity and Recovery",
        "variables": ["readiness_score", "hrv", "steps", "strength_sets"],
        "tests": [("steps", "readiness_score"), ("steps", "hrv"),
                  ("strength_sets", "readiness_score"), ("strength_sets", "hrv")],
    },
    "alcohol": {
        "label": "Alcohol and Recovery",
        "variables": ["sleep_score", "hrv", "pure_alcohol_ml"],
        "tests": [("pure_alcohol_ml", "sleep_score"), ("pure_alcohol_ml", "hrv")],
    },
    "thc": {
        "label": "THC and Recovery",
        "variables": ["sleep_score", "hrv", "thc_mg"],
        "tests": [("thc_mg", "sleep_score"), ("thc_mg", "hrv")],
    },
    "nicotine": {
        "label": "Nicotine and Recovery",
        "variables": ["sleep_score", "hrv", "nicotine_mg"],
        "tests": [("nicotine_mg", "sleep_score"), ("nicotine_mg", "hrv")],
    },
}


def _longest_complete_run(days, rows, variables):
    runs = []
    current = []
    previous = None
    for day, row in zip(days, rows):
        parsed = Date.fromisoformat(day)
        complete = all(row.get(name) is not None and math.isfinite(float(row[name])) for name in variables)
        if not complete or (previous and (parsed - previous).days != 1):
            if current:
                runs.append(current)
            current = []
        if complete:
            current.append((day, [float(row[name]) for name in variables]))
            previous = parsed
        else:
            previous = None
    if current:
        runs.append(current)
    return max(runs, key=len) if runs else []


def _prediction_gain(data, outcome_index, lag):
    targets, restricted, full = [], [], []
    for index in range(lag, len(data)):
        targets.append(data[index, outcome_index])
        restricted.append([1.0] + [data[index - back, outcome_index] for back in range(1, lag + 1)])
        full.append([1.0] + [data[index - back, column]
                             for back in range(1, lag + 1)
                             for column in range(data.shape[1])])
    targets = np.asarray(targets)
    restricted = np.asarray(restricted)
    full = np.asarray(full)
    split = max(20, int(len(targets) * 0.8))
    if len(targets) - split < 8:
        return None
    coef_r, _, _, _ = np.linalg.lstsq(restricted[:split], targets[:split], rcond=None)
    coef_f, _, _, _ = np.linalg.lstsq(full[:split], targets[:split], rcond=None)
    rmse_r = float(np.sqrt(np.mean((targets[split:] - restricted[split:] @ coef_r) ** 2)))
    rmse_f = float(np.sqrt(np.mean((targets[split:] - full[split:] @ coef_f) ** 2)))
    return (rmse_r - rmse_f) * 100 / rmse_r if rmse_r > 0 else None


def run_var_analysis(days, rows, *, model_name="all", max_lag=3, min_obs=60, horizon=7):
    """Fit targeted stationary VAR models with corrected Granger tests and IRFs."""
    import pandas as pd
    from statsmodels.stats.multitest import fdrcorrection
    from statsmodels.tsa.api import VAR
    from statsmodels.tsa.stattools import adfuller

    selected = VAR_MODELS if model_name == "all" else {
        model_name: VAR_MODELS[model_name]
    } if model_name in VAR_MODELS else {}
    payload = {
        "method": "stationary VAR with F-test Granger causality",
        "method_version": METHOD_VERSION,
        "minimum_observations": min_obs,
        "models": [],
        "results": [],
    }

    for key, config in selected.items():
        variables = config["variables"]
        run = _longest_complete_run(days, rows, variables)
        model_info = {"id": key, "label": config["label"], "variables": variables, "observations": len(run)}
        if len(run) < min_obs:
            model_info.update(status="collecting", reason="Not enough contiguous complete daily observations.")
            payload["models"].append(model_info)
            continue

        frame = pd.DataFrame([item[1] for item in run], columns=variables,
                             index=pd.to_datetime([item[0] for item in run])).asfreq("D")
        if any(float(frame[column].std()) <= 1e-9 for column in variables):
            model_info.update(status="collecting", reason="At least one model variable is constant.")
            payload["models"].append(model_info)
            continue

        transformations = {}
        transformed = frame.copy()
        for column in variables:
            try:
                pvalue = float(adfuller(frame[column].values, autolag="AIC")[1])
            except ValueError:
                pvalue = 1.0
            transformations[column] = "difference" if pvalue > 0.05 else "level"
            if pvalue > 0.05:
                transformed[column] = frame[column].diff()
        transformed = transformed.dropna()
        transformed = (transformed - transformed.mean()) / transformed.std(ddof=0)

        feasible_lag = min(max_lag, max(1, (len(transformed) - 5) // (len(variables) + 1)))
        try:
            model = VAR(transformed)
            orders = model.select_order(feasible_lag)
            lag = int(orders.selected_orders.get("bic") or 1)
            lag = max(1, min(lag, feasible_lag))
            fitted = model.fit(lag)
        except (ValueError, np.linalg.LinAlgError) as exc:
            model_info.update(status="failed", reason=str(exc))
            payload["models"].append(model_info)
            continue

        stable = bool(fitted.is_stable())
        whiteness_p = None
        try:
            whiteness_lags = min(10, max(lag + 1, len(transformed) // 8))
            whiteness_p = float(fitted.test_whiteness(nlags=whiteness_lags).pvalue)
        except (ValueError, np.linalg.LinAlgError):
            pass

        model_info.update(
            status="ready",
            lag=lag,
            observations=int(fitted.nobs),
            stable=stable,
            whiteness_p=round(whiteness_p, 4) if whiteness_p is not None else None,
            transformations=transformations,
        )
        payload["models"].append(model_info)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            irf = fitted.irf(horizon).irfs
        for predictor, outcome in config["tests"]:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    test = fitted.test_causality(outcome, [predictor], kind="f")
            except (ValueError, np.linalg.LinAlgError):
                continue
            if not math.isfinite(float(test.pvalue)) or not math.isfinite(float(test.test_statistic)):
                continue
            predictor_index = variables.index(predictor)
            outcome_index = variables.index(outcome)
            response = [float(irf[step, outcome_index, predictor_index]) for step in range(1, horizon + 1)]
            if not all(math.isfinite(value) for value in response):
                continue
            cumulative = sum(response)
            gain = _prediction_gain(transformed.values, outcome_index, lag)
            original_values = frame[predictor].values
            exposure_days = int(np.sum(original_values != 0))
            payload["results"].append({
                "model": key,
                "model_label": config["label"],
                "predictor": predictor,
                "outcome": outcome,
                "lag": lag,
                "n": int(fitted.nobs),
                "exposure_days": exposure_days,
                "f": round(float(test.test_statistic), 3),
                "p": float(test.pvalue),
                "prediction_gain_pct": round(gain, 1) if gain is not None else None,
                "direction": "higher" if cumulative > 0 else "lower",
                "cumulative_response": round(cumulative, 4),
                "irf": [{"day": index + 1, "response": round(value, 4)}
                        for index, value in enumerate(response)],
                "stable": stable,
                "whiteness_p": round(whiteness_p, 4) if whiteness_p is not None else None,
            })

    if payload["results"]:
        rejected, corrected = fdrcorrection([item["p"] for item in payload["results"]], alpha=0.10)
        for item, keep, qvalue in zip(payload["results"], rejected, corrected):
            item["p"] = float(f"{item['p']:.5g}")
            item["q"] = float(f"{float(qvalue):.5g}")
            diagnostics_pass = item["stable"] and (
                item["whiteness_p"] is None or item["whiteness_p"] >= 0.05
            )
            item["supported"] = bool(
                keep and diagnostics_pass and (item["prediction_gain_pct"] or 0) > 0
            )
        payload["results"].sort(key=lambda item: (item["q"], -(item["prediction_gain_pct"] or -999)))

    payload["status"] = "ready" if payload["results"] else "collecting"
    payload["total_days"] = len(days)
    payload["mature_days"] = min_obs
    payload["max_lag"] = max_lag
    return payload
