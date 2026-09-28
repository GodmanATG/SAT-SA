"""
Feature engineering (proposal Stage 3)
======================================
Turns normalised alerts / cases / escalations / inventory into the entity-level
and entity-month features that every detector and score depends on.

Everything is vectorised pandas - no per-row Python loops - because the tool has
to run across dozens of entities and several review periods.  The previous build
computed part of this inside rule functions and left several ``entity_metrics``
columns permanently at 0, which silently disabled the peer and trend detectors.

Conventions
-----------
* ``*_rate``  -> fraction in 0-1
* ``*_pct``   -> percentage in 0-100
* all times   -> minutes
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd

# Work whether imported as `detection.features` (app) or run directly (tests/CLI)
_DET_DIR = pathlib.Path(__file__).resolve().parent
for _p in (str(_DET_DIR.parent), str(_DET_DIR)):
    if _p not in sys.path:
        sys.path.append(_p)

from common import safe_div  # noqa: E402  (path bootstrapped above)

# Share of activity a genuinely 24x7 monitored estate shows outside 22:00-06:00
EXPECTED_NIGHT_SHARE = 8.0 / 24.0

CASE_DEPTH_WEIGHTS = dict(notes=0.3, root_cause=0.3, remediation=0.2, escalation=0.2)


# ---------------------------------------------------------------------------
# Alert-level preparation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Entity-scoped join keys
#
# The natural key of a submission record is (entity_id, record id). Two CSEs will
# both submit an "ALT-0001", so any join between two canonical tables MUST carry the
# entity with the id: without it, entity A's alert is silently linked to entity B's
# case or escalation, and one entity's missing evidence is excused by another's.
# These three helpers are the only way cross-table linkage is done in this module.
# ---------------------------------------------------------------------------

KEY_SEP = "\u0000"


def scoped_keys(frame: pd.DataFrame, id_col: str) -> pd.Series:
    """``entity_id`` + record id — the join key for anything spanning two tables."""
    if id_col in frame.columns:
        ids = frame[id_col].fillna("").astype(str).str.strip()
    else:
        ids = pd.Series("", index=frame.index, dtype=object)
    return frame["entity_id"].astype(str) + KEY_SEP + ids


def scoped_key_set(frame: pd.DataFrame | None, id_col: str) -> set:
    """Non-empty entity-scoped ids from one canonical table (for ``isin`` tests)."""
    if frame is None or frame.empty or id_col not in frame.columns:
        return set()
    ids = frame[id_col].fillna("").astype(str).str.strip()
    keep = ids.ne("")
    if not keep.any():
        return set()
    sub = frame.loc[keep]
    return set(scoped_keys(sub, id_col))


def scoped_rank(frame: pd.DataFrame, id_col: str, value_col: str) -> pd.Series:
    """Max ``value_col`` per entity-scoped id (for ``.map`` lookups)."""
    if frame is None or frame.empty or id_col not in frame.columns \
            or value_col not in frame.columns:
        return pd.Series(dtype=float)
    ids = frame[id_col].fillna("").astype(str).str.strip()
    keep = ids.ne("")
    if not keep.any():
        return pd.Series(dtype=float)
    sub = frame.loc[keep]
    return pd.to_numeric(sub[value_col], errors="coerce").fillna(0) \
        .groupby(scoped_keys(sub, id_col)).max()


def enrich_alerts(alerts: pd.DataFrame, cases: pd.DataFrame,
                  escalations: pd.DataFrame, thresholds: dict) -> pd.DataFrame:
    """Attach every derived alert flag used by the detectors."""
    if alerts is None or alerts.empty:
        return pd.DataFrame()

    df = alerts.copy().reset_index(drop=True)
    df["created_dt"] = pd.to_datetime(df["created_ts"], errors="coerce")
    df["ack_dt"] = pd.to_datetime(df.get("acknowledged_ts"), errors="coerce")
    df["close_dt"] = pd.to_datetime(df["closed_ts"], errors="coerce")

    df["ack_minutes"] = (df["ack_dt"] - df["created_dt"]).dt.total_seconds() / 60.0
    df["close_minutes"] = (df["close_dt"] - df["created_dt"]).dt.total_seconds() / 60.0
    # impossible (negative) durations are data-quality noise, not evidence
    df.loc[df["ack_minutes"] < 0, "ack_minutes"] = np.nan
    df.loc[df["close_minutes"] < 0, "close_minutes"] = np.nan

    sev = df["severity"].astype(str).str.lower()
    df["severity"] = sev
    df["is_critical"] = sev == "critical"
    df["is_crit_high"] = sev.isin(["critical", "high"])

    fast_limit = float(thresholds.get("fast_closure_minutes", 5))
    df["fast_flag"] = df["is_crit_high"] & (df["close_minutes"] < fast_limit)
    df["close_minutes_critical"] = df["close_minutes"].where(df["is_critical"])
    # Acknowledgement is benchmarked on the alerts that actually demand an analyst
    df["ack_minutes_crit_high"] = df["ack_minutes"].where(df["is_crit_high"])

    df["created_hour"] = df["created_dt"].dt.hour
    df["is_night"] = (df["created_hour"] >= 22) | (df["created_hour"] < 6)
    # Weekend blindness (rule NS-007) is a distinct failure from a night gap: a SOC
    # can staff nights and still stop collecting at the weekend.
    df["is_weekend"] = df["created_dt"].dt.weekday >= 5
    df["month"] = df["created_dt"].dt.strftime("%Y-%m")

    # ── escalation linkage: escalated if the alert OR its case has a record ──
    alert_key = scoped_keys(df, "alert_id")
    case_key = scoped_keys(df, "case_id")
    esc_alerts = scoped_key_set(escalations, "alert_id")
    esc_cases = scoped_key_set(escalations, "case_id")
    df["escalated"] = alert_key.isin(esc_alerts) | case_key.isin(esc_cases)
    df["esc_critical_flag"] = df["escalated"] & df["is_critical"]

    # ── case linkage: root cause documented, and whether a case exists at all ──
    if cases is not None and not cases.empty:
        cases_keyed = cases.copy()
        cases_keyed["_key"] = scoped_keys(cases_keyed, "case_id")
        root_map = (cases_keyed.drop_duplicates("_key")
                    .set_index("_key")["root_cause_documented"].to_dict())
        remediation_map = (cases_keyed.drop_duplicates("_key")
                           .set_index("_key")["remediation_documented"].to_dict()
                           if "remediation_documented" in cases_keyed.columns else {})
    else:
        root_map, remediation_map = {}, {}
    df["root_cause_flag"] = case_key.map(root_map).fillna(0).astype(bool)
    df["remediation_flag"] = case_key.map(remediation_map).fillna(0).astype(bool)
    df["has_case"] = df["case_id"].notna() & (df["case_id"].astype(str).str.strip() != "")
    df["missing_case_flag"] = df["is_crit_high"] & ~df["has_case"]

    # ── repeat alerts: chronic (asset, category) pairs, i.e. a small set of assets
    #    absorbing a disproportionate share of a category with no root cause on file.
    #    Concentration-based rather than density-based on purpose: an absolute
    #    "N alerts in 30 days" rule simply punishes whichever entity reports more.
    df["is_repeat"] = _mark_chronic_repeats(
        df,
        min_count=int(thresholds.get("repeat_asset_min_count", 10)),
        concentration=float(thresholds.get("repeat_pair_concentration", 4.0)),
        max_root_cause_rate=float(thresholds.get("repeat_pair_max_root_cause_rate", 0.2)),
    )
    return df


def _mark_chronic_repeats(df: pd.DataFrame, min_count: int, concentration: float,
                          max_root_cause_rate: float) -> pd.Series:
    """Flag every alert after the first one on a chronic (asset, category) pair.

    A pair is *chronic* when it carries at least ``min_count`` alerts, at least
    ``concentration`` times the entity's median pair volume, and almost no documented
    root cause. All of it is grouped pandas work - no per-row Python loop.
    """
    if df.empty:
        return pd.Series(dtype=bool)

    key = ["entity_id", "asset_id", "alert_category"]
    relevant = df[df["asset_id"].astype(str).str.strip().ne("")]
    if relevant.empty:
        return pd.Series(False, index=df.index)

    grouped = relevant.groupby(key, dropna=False)
    stats = grouped.agg(count=("alert_id", "size"), root_rate=("root_cause_flag", "mean"))
    median_count = stats.groupby("entity_id")["count"].transform("median")
    threshold = np.maximum(min_count, concentration * median_count)
    chronic_pairs = stats[(stats["count"] >= threshold) &
                          (stats["root_rate"] <= max_root_cause_rate)].index

    if len(chronic_pairs) == 0:
        return pd.Series(False, index=df.index)

    pair_index = pd.MultiIndex.from_frame(relevant[key])
    in_chronic = pair_index.isin(chronic_pairs)
    first_seen = relevant.groupby(key, dropna=False)["created_dt"].transform("min")
    not_first = relevant["created_dt"].ne(first_seen)

    flagged = pd.Series(False, index=df.index)
    flagged.loc[relevant.index[in_chronic & not_first]] = True
    return flagged


def _day_counts(start, end) -> tuple[int, int]:
    """Number of (weekday, weekend) calendar days in an inclusive observed span."""
    if start is None or end is None or pd.isna(start) or pd.isna(end):
        return 0, 0
    days = pd.date_range(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize(), freq="D")
    weekend = int((days.weekday >= 5).sum())
    return int(len(days) - weekend), weekend


def weekend_features(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Weekend vs weekday activity rate per group (rule NS-007).

    Measured as a *rate per day*, not a share of volume, because a share would be
    arithmetically forced down by the two weekend days themselves. The ratio is what
    tells a supervisor whether the estate keeps reporting when the office is shut.
    """
    if df is None or df.empty or "is_weekend" not in df.columns:
        return pd.DataFrame()
    ordered = df.dropna(subset=["created_dt"])
    if ordered.empty:
        return pd.DataFrame()
    rows = []
    for key_vals, sub in ordered.groupby(keys, dropna=False):
        wd_days, we_days = _day_counts(sub["created_dt"].min(), sub["created_dt"].max())
        we_alerts = int(sub["is_weekend"].sum())
        wd_alerts = int(len(sub) - we_alerts)
        wd_rate = wd_alerts / wd_days if wd_days else 0.0
        we_rate = we_alerts / we_days if we_days else 0.0
        # A span that contains no weekend days cannot support a conclusion either way,
        # so it reports "no gap" rather than an absent-evidence false positive.
        ratio = (round(we_rate / wd_rate, 4) if (wd_rate and we_days) else 1.0)
        rec = dict(zip(keys, key_vals if isinstance(key_vals, tuple) else (key_vals,)))
        rec.update(weekend_alerts=we_alerts, weekend_activity_ratio=ratio)
        rows.append(rec)
    return pd.DataFrame(rows)


def analyst_features(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Critical-alert ownership concentration per group (rule EG-007).

    Computed on critical/high alerts only: concentration of *routine* traffic says
    little, while one analyst absorbing almost every severe alert is a staffing and
    resilience finding in its own right.
    """
    if df is None or df.empty or "assigned_analyst_id" not in df.columns:
        return pd.DataFrame()
    sig = df[df["is_crit_high"].fillna(False)].copy()
    sig["assigned_analyst_id"] = sig["assigned_analyst_id"].fillna("").astype(str).str.strip()
    sig = sig[sig["assigned_analyst_id"] != ""]
    if sig.empty:
        return pd.DataFrame()

    per_analyst = (sig.groupby(keys + ["assigned_analyst_id"], dropna=False)
                   .size().rename("n").reset_index())
    agg = (per_analyst.groupby(keys, dropna=False)
           .agg(critical_alerts_assigned=("n", "sum"),
                critical_analysts_active=("n", "size"),
                critical_alerts_top_analyst=("n", "max"))
           .reset_index())
    agg["top_analyst_share"] = (agg["critical_alerts_top_analyst"]
                                / agg["critical_alerts_assigned"].replace(0, np.nan)
                                ).fillna(0.0).round(4)
    return agg


def alert_features(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Alert-derived features grouped by ``keys`` (entity_id or [entity_id, month])."""
    if df is None or df.empty:
        return pd.DataFrame()
    g = df.groupby(keys, dropna=False)
    out = g.agg(
        total_alerts=("alert_id", "size"),
        crit_high=("is_crit_high", "sum"),
        critical=("is_critical", "sum"),
        fast=("fast_flag", "sum"),
        escalated_critical=("esc_critical_flag", "sum"),
        night=("is_night", "sum"),
        repeats=("is_repeat", "sum"),
        missing_case=("missing_case_flag", "sum"),
        root_cause=("root_cause_flag", "sum"),
        escalated_any=("escalated", "sum"),
        ack_p50=("ack_minutes", "median"),
        ack_p50_crit_high=("ack_minutes_crit_high", "median"),
        close_p50=("close_minutes", "median"),
        close_p50_critical=("close_minutes_critical", "median"),
    )
    repeat_assets = df[df["is_repeat"]].groupby(keys, dropna=False)["asset_id"].nunique()
    out["repeat_assets"] = repeat_assets.reindex(out.index).fillna(0)
    repeat_categories = df[df["is_repeat"]].groupby(keys, dropna=False)["alert_category"].nunique()
    out["repeat_categories"] = repeat_categories.reindex(out.index).fillna(0)
    out["fast_closure_rate"] = (out["fast"] / out["crit_high"].replace(0, np.nan)).fillna(0.0)
    out["escalation_rate_critical"] = (
        out["escalated_critical"] / out["critical"].replace(0, np.nan)).fillna(0.0)
    out["crit_no_escalation_rate"] = 1.0 - out["escalation_rate_critical"]
    out.loc[out["critical"] == 0, "crit_no_escalation_rate"] = 0.0
    out["repeat_alert_rate"] = (out["repeats"] / out["total_alerts"].replace(0, np.nan)).fillna(0.0)
    out["missing_case_rate"] = (out["missing_case"] / out["crit_high"].replace(0, np.nan)).fillna(0.0)
    out["root_cause_rate"] = (out["root_cause"] / out["total_alerts"].replace(0, np.nan)).fillna(0.0)
    night_share = out["night"] / out["total_alerts"].replace(0, np.nan)
    out["night_coverage_gap_pct"] = (
        ((EXPECTED_NIGHT_SHARE - night_share) / EXPECTED_NIGHT_SHARE).clip(lower=0) * 100
    ).fillna(0.0)
    out["time_to_ack_median"] = out["ack_p50"].fillna(0.0)
    out["time_to_ack_median_critical"] = out["ack_p50_crit_high"].fillna(0.0)
    out["time_to_close_median"] = out["close_p50"].fillna(0.0)
    out["time_to_close_median_critical"] = out["close_p50_critical"].fillna(0.0)
    return out.reset_index()


# ---------------------------------------------------------------------------
# Case-level preparation
# ---------------------------------------------------------------------------

def note_similarity_rate(notes: pd.Series, threshold: float,
                         max_notes: int = 5000, seed: int = 42) -> tuple[float, list[str], bool]:
    """Share of investigation notes in duplicate clusters (TF-IDF + cosine).

    Returns (rate, case ids flagged, whether the pass was sampled).  Falls back to
    exact-normalised matching if
    scikit-learn is unavailable, so the tool still runs on a bare install.

    The pairwise cosine matrix is O(n^2), so it is computed on a **bounded random
    sample** of at most ``max_notes`` notes per entity (deterministic seed, so a
    re-run reproduces the same number). Without the cap a single large CSE would
    allocate gigabytes and take the whole run down, which is precisely the scale
    case the tool exists for. The sample size is stated in the finding rationale so
    the estimate is never presented as an exact population figure.
    """
    notes = notes.fillna("").astype(str).str.strip()
    mask = notes.str.len() > 0
    if mask.sum() < 2:
        return 0.0, [], False

    subset = notes[mask]
    sampled = False
    if max_notes and len(subset) > max_notes:
        subset = subset.sample(n=int(max_notes), random_state=seed)
        sampled = True

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        matrix = TfidfVectorizer(stop_words="english").fit_transform(subset)
        sim = cosine_similarity(matrix)
        np.fill_diagonal(sim, 0)
        dup_mask = sim.max(axis=1) >= threshold
    except Exception:
        norm = subset.str.lower().str.replace(r"\W+", " ", regex=True).str.strip()
        dup_mask = norm.duplicated(keep=False).values

    dup_ids = subset.index[dup_mask].tolist()
    return safe_div(len(dup_ids), len(subset)), list(dup_ids), sampled


def case_features(cases: pd.DataFrame, escalations: pd.DataFrame,
                  thresholds: dict) -> pd.DataFrame:
    """Case/investigation features per entity (depth, similarity, reopen rate)."""
    if cases is None or cases.empty:
        return pd.DataFrame()

    df = cases.copy().reset_index(drop=True)
    escalation_case_ids = scoped_key_set(escalations, "case_id")

    df["has_notes"] = df["investigation_note_text"].fillna("").astype(str).str.strip().ne("")
    df["root_cause"] = df["root_cause_documented"].fillna(0).astype(int)
    df["remediation"] = df.get("remediation_documented", pd.Series(0, index=df.index)).fillna(0).astype(int)
    df["has_escalation"] = scoped_keys(df, "case_id").isin(escalation_case_ids).astype(int)
    df["depth_score"] = (
        df["has_notes"].astype(int) * CASE_DEPTH_WEIGHTS["notes"]
        + df["root_cause"] * CASE_DEPTH_WEIGHTS["root_cause"]
        + df["remediation"] * CASE_DEPTH_WEIGHTS["remediation"]
        + df["has_escalation"] * CASE_DEPTH_WEIGHTS["escalation"]
    )
    df["reopen_count"] = pd.to_numeric(df.get("reopened_count", 0), errors="coerce").fillna(0)

    rows = []
    threshold = float(thresholds.get("template_cosine_threshold", 0.85))
    max_notes = int(thresholds.get("template_note_sample_cap", 5000))
    for entity_id, group in df.groupby("entity_id", dropna=False):
        rate, dup_case_ids, sampled = note_similarity_rate(group["investigation_note_text"],
                                                          threshold, max_notes=max_notes)
        rows.append(dict(
            entity_id=entity_id,
            total_cases=len(group),
            notes_present_rate=float(group["has_notes"].mean()),
            # Provenance of the similarity estimate comes back from the pass itself, so the
            # reported sample size can never disagree with the sample actually compared.
            note_similarity_sample_size=int(min(len(group), max_notes)) if max_notes else len(group),
            note_similarity_sampled=bool(sampled),
            investigation_note_similarity=round(rate, 4),
            template_note_rate=round(rate, 4),
            avg_investigation_depth=round(float(group["depth_score"].mean()), 4),
            root_cause_case_rate=round(float(group["root_cause"].mean()), 4),
            remediation_rate=round(float(group["remediation"].mean()), 4),
            # mean number of reopens per case - distinct from ``reopen_rate``, which is
            # the share of closures reopened (computed from dispositions, below). Two
            # features with the same name would silently collide on merge.
            case_reopen_mean=round(float(group["reopen_count"].mean()), 4),
            _duplicate_case_ids=dup_case_ids,
        ))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Disposition / closure features (SLA integrity, reopens, accepted risk)
# ---------------------------------------------------------------------------

SEV_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}


def _bool_col(frame: pd.DataFrame, col: str) -> pd.Series:
    """Read a column that may hold 0/1, 'true'/'false' or true/false."""
    if col not in frame.columns:
        return pd.Series(0, index=frame.index)
    series = frame[col]
    if series.dtype == bool:
        return series.astype(int)
    return (series.astype(str).str.strip().str.lower()
            .isin(["1", "1.0", "true", "yes", "y", "t"])).astype(int)


def _text_col(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series("", index=frame.index, dtype=object)
    return frame[col].fillna("").astype(str).str.strip()


def severity_rank(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.lower().map(SEV_RANK).fillna(0)


def disposition_features(dispositions: pd.DataFrame, alerts: pd.DataFrame) -> pd.DataFrame:
    """Closure-integrity features: measured SLA, misreported SLA, reopens, accepted risk.

    The interesting measure here is not the SLA breach rate on its own but the
    **misreport rate**: closure records that claim compliance while the recorded
    time-to-close exceeds the recorded target. That is the textbook "operational
    behaviour designed to satisfy metrics without reducing risk".
    """
    if dispositions is None or dispositions.empty:
        return pd.DataFrame()

    df = dispositions.copy().reset_index(drop=True)
    df["measured_close"] = pd.to_numeric(df.get("time_to_close_minutes"), errors="coerce")
    df["target"] = pd.to_numeric(df.get("sla_target_minutes"), errors="coerce")
    df["reported_sla"] = _bool_col(df, "made_sla")
    df["risk_accepted"] = _bool_col(df, "risk_accepted")
    df["true_positive"] = _bool_col(df, "true_positive")
    df["reopen_count"] = pd.to_numeric(df.get("reopen_count"), errors="coerce").fillna(0)
    df["root_cause_text"] = _text_col(df, "root_cause")
    df["authority"] = _text_col(df, "risk_acceptance_authority")
    df["remediation_status"] = _text_col(df, "remediation_status").str.lower()
    df["remediation_ref"] = _text_col(df, "remediation_reference")

    # Only closures with a target are measurable; an entity that submits no target
    # is not therefore compliant.
    measurable = df["target"] > 0
    df["sla_breach"] = (measurable & (df["measured_close"] > df["target"])).astype(int)
    df["sla_misreport"] = ((df["sla_breach"] == 1) & (df["reported_sla"] == 1)).astype(int)
    df["reopen_flag"] = (df["reopen_count"] > 0).astype(int)
    df["no_authority"] = ((df["risk_accepted"] == 1) & (df["authority"] == "")).astype(int)

    # "Significant" closures: those resolving a critical/high alert, or confirmed
    # true positives - the closures an examiner would expect to carry a root cause.
    df["alert_rank"] = 0.0
    if alerts is not None and not alerts.empty and "alert_id" in alerts.columns:
        graded = alerts.copy()
        graded["alert_rank"] = severity_rank(graded["severity"])
        alert_rank = scoped_rank(graded, "alert_id", "alert_rank")
        df["alert_rank"] = scoped_keys(df, "alert_id").map(alert_rank).fillna(0)
    df["significant"] = ((df["alert_rank"] >= 2) | (df["true_positive"] == 1)).astype(int)
    sig = df["significant"] == 1
    df["no_root_cause"] = (sig & (df["root_cause_text"] == "")).astype(int)
    df["no_remediation"] = (sig & (~df["remediation_status"].isin(["completed", "in_progress"]))
                            & (df["remediation_ref"] == "")).astype(int)

    def _rate(sub: pd.DataFrame, mask_col: str, denom: int) -> float:
        return round(safe_div(float(sub[mask_col].sum()), denom), 4)

    rows = []
    for entity_id, group in df.groupby("entity_id", dropna=False):
        measurable_rows = int((group["target"] > 0).sum())
        reported_rows = int((group["reported_sla"] == 1).sum())
        sig_rows = int(group["significant"].sum())
        accepted_rows = int(group["risk_accepted"].sum())
        rows.append(dict(
            entity_id=entity_id,
            total_dispositions=len(group),
            sla_breach_rate=_rate(group, "sla_breach", max(measurable_rows, 1))
            if measurable_rows else 0.0,
            sla_misreport_rate=_rate(group, "sla_misreport", max(reported_rows, 1))
            if reported_rows else 0.0,
            sla_misreport_count=int(group["sla_misreport"].sum()),
            close_time_over_target_median=round(float(
                (group.loc[group["sla_breach"] == 1, "measured_close"]
                 / group.loc[group["sla_breach"] == 1, "target"]).median()), 2)
            if (group["sla_breach"] == 1).any() and measurable_rows else 0.0,
            true_positive_rate=round(float(group["true_positive"].mean()), 4),
            risk_accept_rate=round(float(group["risk_accepted"].mean()), 4),
            risk_accept_no_authority_rate=_rate(group, "no_authority", max(accepted_rows, 1))
            if accepted_rows else 0.0,
            no_root_cause_gap=_rate(group, "no_root_cause", max(sig_rows, 1)) if sig_rows else 0.0,
            no_remediation_gap=_rate(group, "no_remediation", max(sig_rows, 1)) if sig_rows else 0.0,
            significant_closures=sig_rows,
            reopen_rate=round(float(group["reopen_flag"].mean()), 4),
            reopened_closures=int(group["reopen_flag"].sum()),
        ))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Investigation workflow features (process mining)
# ---------------------------------------------------------------------------

def rework_loop_cases(frame: pd.DataFrame, case_col: str = "case_id",
                      activity_col: str = "activity_type",
                      sequence_col: str = "sequence_number") -> pd.Series:
    """Case ids whose activity log repeats a step with a different step in between.

    That is the signature of an investigation sent back and forth
    (triage -> enrichment -> triage) rather than one that progressed.

    Vectorised rather than per-case Python: within each case the step positions are
    taken from the sequence number, and a loop exists exactly when the same activity
    appears at positions more than one apart. So ``max(position) - min(position) > 1``
    per (case, activity) is the whole test, and it is one groupby aggregation for the
    entire estate instead of a loop per investigation.
    """
    if frame is None or frame.empty:
        return pd.Series(dtype=object)
    work = frame[[case_col, activity_col]].copy()
    work[activity_col] = work[activity_col].fillna("").astype(str).str.strip()
    work = work[work[activity_col] != ""]
    if work.empty:
        return pd.Series(dtype=object)
    if sequence_col in frame.columns:
        work["pos"] = pd.to_numeric(frame[sequence_col], errors="coerce")
        # Fall back to row order wherever the export omits or garbles sequence numbers
        work["pos"] = work["pos"].where(work["pos"].notna(), work.groupby(case_col).cumcount())
    else:
        work["pos"] = work.groupby(case_col).cumcount()

    span = work.groupby([case_col, activity_col], dropna=False)["pos"].agg(["min", "max"])
    looping = span[(span["max"] - span["min"]) > 1]
    if looping.empty:
        return pd.Series(dtype=object)
    return pd.Series(looping.index.get_level_values(0).unique())


def investigation_features(investigations: pd.DataFrame, cases: pd.DataFrame,
                           thresholds: dict) -> pd.DataFrame:
    """Workflow volume, evidence completeness and rework loops per entity."""
    if (investigations is None or investigations.empty) and (cases is None or cases.empty):
        return pd.DataFrame()

    min_events = int(thresholds.get("investigation_event_min", 2))
    min_investigation_minutes = float(thresholds.get("investigation_time_min_minutes", 2.0))
    case_index = pd.DataFrame()
    if cases is not None and not cases.empty:
        # de-duplicated on the *entity-scoped* key: a case id shared with another CSE
        # must not drop either entity's case from its own denominator
        case_index = cases[["case_id", "entity_id"]].drop_duplicates(["entity_id", "case_id"]).copy()

    rows = []
    entity_ids = set()
    if not case_index.empty:
        entity_ids |= set(case_index["entity_id"])
    if investigations is not None and not investigations.empty:
        entity_ids |= set(investigations["entity_id"])

    inv = investigations.copy() if investigations is not None and not investigations.empty \
        else pd.DataFrame(columns=["entity_id", "case_id", "activity_type", "evidence_type",
                                   "action_result"])
    inv["evidence_type"] = _text_col(inv, "evidence_type")
    inv["action_result"] = _text_col(inv, "action_result")
    inv["activity_type"] = _text_col(inv, "activity_type")
    inv["evidence_gap"] = ((inv["evidence_type"] == "") | (inv["action_result"] == "")).astype(int)

    for entity_id in sorted(entity_ids):
        ent_cases = case_index[case_index["entity_id"] == entity_id] if not case_index.empty \
            else pd.DataFrame(columns=["case_id"])
        ent_inv = inv[inv["entity_id"] == entity_id]
        per_case = ent_inv.groupby("case_id").agg(events=("activity_type", "size"),
                                                   evidence_gaps=("evidence_gap", "sum"))
        n_cases = max(len(ent_cases), 1)
        cases_with_events = per_case.index.nunique()
        silent_cases = max(0, len(ent_cases) - cases_with_events)
        thin_cases = int((per_case["events"] < min_events).sum()) if len(per_case) else 0

        loops = len(rework_loop_cases(ent_inv)) if not ent_inv.empty else 0

        # Investigation time (rule IM-009): a case can carry a complete-looking
        # workflow record while the time recorded against it is seconds.
        durations = pd.to_numeric(ent_inv.get("duration_minutes"), errors="coerce") \
            if not ent_inv.empty else pd.Series(dtype=float)
        per_case_minutes = (ent_inv.assign(_d=durations.fillna(0)).groupby("case_id")["_d"].sum()
                            if not ent_inv.empty else pd.Series(dtype=float))
        thin = int((per_case_minutes < min_investigation_minutes).sum()) if len(per_case_minutes) else 0

        rows.append(dict(
            entity_id=entity_id,
            investigation_events=int(len(ent_inv)),
            cases_with_events=int(cases_with_events),
            cases_without_investigation=silent_cases,
            avg_events_per_case=round(safe_div(len(ent_inv), n_cases), 2),
            investigation_gap_rate=round(safe_div(silent_cases + thin_cases, n_cases), 4),
            evidence_gap_rate=round(safe_div(int(ent_inv["evidence_gap"].sum()), len(ent_inv)), 4)
            if len(ent_inv) else 0.0,
            rework_loop_rate=round(safe_div(loops, max(cases_with_events, 1)), 4),
            rework_cases=int(loops),
            cases_with_thin_investigation=thin,
            investigation_time_anomaly_rate=round(
                safe_div(thin, max(cases_with_events, 1)), 4),
            investigation_minutes_median=round(
                float(per_case_minutes.median()), 2) if len(per_case_minutes) else 0.0,
        ))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Severity softening / escalation downgrade
# ---------------------------------------------------------------------------

def severity_softening_features(cases: pd.DataFrame, alerts: pd.DataFrame,
                                escalations: pd.DataFrame) -> pd.DataFrame:
    """Cases (and escalations) recorded below the severity of the evidence behind them.

    A case logged at low priority for a critical alert is the quietest execution gap
    there is: every downstream metric (closure time, escalation rate) then looks
    healthy because the incident was re-labelled on the way in.
    """
    if cases is None or cases.empty:
        return pd.DataFrame()

    df = cases.copy()
    df["case_rank"] = severity_rank(df["severity"])

    alert_max_rank = pd.Series(dtype=float)
    if alerts is not None and not alerts.empty:
        linked = alerts[alerts["case_id"].astype(str).str.strip() != ""].copy()
        linked["alert_rank"] = severity_rank(linked["severity"])
        alert_max_rank = scoped_rank(linked, "case_id", "alert_rank")
    df["alert_rank"] = scoped_keys(df, "case_id").map(alert_max_rank).fillna(0)

    # only cases opened from a critical/high alert can be softened
    significant = df["alert_rank"] >= 2
    df["softened"] = (significant & (df["case_rank"] < df["alert_rank"])).astype(int)

    downgrade = pd.Series(dtype=float)
    if escalations is not None and not escalations.empty and "decision" in escalations.columns:
        decision = escalations["decision"].fillna("").astype(str).str.lower()
        downgrade = (decision.isin(["downgraded", "closed at source", "reassigned"])) \
            .groupby(escalations["entity_id"]).mean()

    rows = []
    for entity_id, group in df.groupby("entity_id", dropna=False):
        sig = int((group["alert_rank"] >= 2).sum())
        rows.append(dict(
            entity_id=entity_id,
            significant_cases=len(group),
            cases_from_significant_alerts=sig,
            severity_softening_rate=round(safe_div(int(group["softened"].sum()), sig), 4)
            if sig else 0.0,
            severity_softening_count=int(group["softened"].sum()),
            escalation_downgrade_rate=round(float(downgrade.get(entity_id, 0.0)), 4),
        ))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Inventory features
# ---------------------------------------------------------------------------

def inventory_features(assets: pd.DataFrame, alerts: pd.DataFrame) -> pd.DataFrame:
    """Asset estate features, including the negative-space counts."""
    if assets is None or assets.empty:
        return pd.DataFrame()

    df = assets.copy()
    df["criticality_tier"] = df["criticality_tier"].fillna("").astype(str)
    df["criticality_tier"] = df.apply(
        lambda r: r["criticality_tier"] or str(r.get("business_criticality", "")), axis=1)
    critical = df["criticality_tier"].str.contains("critical|tier.?1", case=False, na=False, regex=True)
    df["is_critical"] = critical
    df["is_ot"] = df["asset_class"].astype(str).str.lower().str.startswith("critical_ot")
    df["monitored"] = df["monitoring_status"].astype(str).str.lower().isin(
        ["active", "enabled", "true", "1", "yes", ""])
    # "declared for monitoring but not actually monitored" needs both signals, since an
    # asset can be missing from monitoring without anything saying so explicitly
    df["required"] = _text_col(df, "monitoring_required").str.lower().isin(
        ["true", "yes", "1", "y", ""])
    df["telemetry_seen"] = _text_col(df, "last_telemetry_timestamp") != ""

    alerted_assets = scoped_key_set(alerts, "asset_id")
    df["has_alerts"] = scoped_keys(df, "asset_id").isin(alerted_assets)

    rows = []
    for entity_id, group in df.groupby("entity_id", dropna=False):
        crit = group["is_critical"]
        unmonitored = crit & group["required"] & (~group["monitored"] | ~group["telemetry_seen"])
        rows.append(dict(
            entity_id=entity_id,
            total_assets=len(group),
            critical_assets=int(crit.sum()),
            monitored_assets=int(group["monitored"].sum()),
            silent_critical_assets=int((crit & ~group["has_alerts"]).sum()),
            unmonitored_critical_assets=int(unmonitored.sum()),
            ot_assets=int((group["asset_class"].astype(str).str.lower() == "critical_ot").sum()),
            monitoring_coverage_pct=round(safe_div(int(group["monitored"].sum()), len(group)) * 100, 1),
            critical_monitoring_coverage_pct=round(
                safe_div(int((crit & group["monitored"]).sum()), int(crit.sum())) * 100, 1)
            if int(crit.sum()) else 100.0,
            telemetry_silence_pct=round(
                safe_div(int((group["monitored"] & ~group["telemetry_seen"]).sum()),
                         int(group["monitored"].sum())) * 100, 1)
            if int(group["monitored"].sum()) else 0.0,
        ))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def entity_features(alerts, cases, escalations, assets, thresholds,
                    investigations=None, dispositions=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Full entity-level feature table + enriched alerts (reused by detectors).

    Every feature family is merged on ``entity_id`` with an outer join, so an entity
    that submitted only some artefacts still gets a row (a missing investigation or
    disposition file is itself a finding, not a reason to drop the entity).
    """
    enriched = enrich_alerts(alerts, cases, escalations, thresholds)
    if enriched.empty and (cases is None or cases.empty):
        return pd.DataFrame(), enriched

    parts = [alert_features(enriched, ["entity_id"])]
    parts.append(weekend_features(enriched, ["entity_id"]))
    parts.append(analyst_features(enriched, ["entity_id"]))
    parts.append(case_features(cases, escalations, thresholds))
    parts.append(inventory_features(assets, alerts))
    parts.append(disposition_features(dispositions, alerts))
    parts.append(investigation_features(investigations, cases, thresholds))
    parts.append(severity_softening_features(cases, alerts, escalations))

    out = pd.DataFrame()
    for part in parts:
        if part is None or part.empty:
            continue
        part = part.drop(columns=[c for c in part.columns if c.startswith("_") and c != "_duplicate_case_ids"])
        out = part if out.empty else out.merge(part, on="entity_id", how="outer")
    if out.empty:
        return out, enriched
    out = _coalesce_suffixed(out)
    return out.fillna(0), enriched


def _coalesce_suffixed(frame: pd.DataFrame) -> pd.DataFrame:
    """Recombine columns that a merge suffixed as ``name_x`` / ``name_y``.

    Two feature families can legitimately produce the same column name. Without this
    step pandas renames both to ``name_x``/``name_y`` and the plain ``name`` simply
    disappears, so a detector reading ``metrics['name']`` silently sees zero - the
    failure mode that had already disabled one metric in an earlier build. The two
    sides are merged with the first non-null winning.
    """
    suffixed = [c for c in frame.columns if c.endswith("_x") or c.endswith("_y")]
    if not suffixed:
        return frame
    bases = sorted({c[:-2] for c in suffixed})
    for base in bases:
        left, right = f"{base}_x", f"{base}_y"
        merged = pd.Series(np.nan, index=frame.index)
        for col in (left, right):
            if col in frame.columns:
                merged = merged.fillna(frame[col])
        frame[base] = merged
        frame = frame.drop(columns=[c for c in (left, right) if c in frame.columns])
    return frame


def monthly_case_features(cases: pd.DataFrame, dispositions: pd.DataFrame,
                          investigations: pd.DataFrame, keys) -> pd.DataFrame:
    """Entity x month view of the case/closure/workflow measures used by trends."""
    out = pd.DataFrame()
    if cases is not None and not cases.empty:
        cf = cases.copy()
        cf["month"] = pd.to_datetime(cf.get("opened_ts"), errors="coerce").dt.strftime("%Y-%m")
        cf = cf.dropna(subset=["month"])
        if not cf.empty:
            out = (cf.groupby(keys, dropna=False)
                   .agg(total_cases=("case_id", "size"),
                        reopened_cases=("reopened_count", lambda s: int(
                            pd.to_numeric(s, errors="coerce").fillna(0).gt(0).sum()))))
            out["reopen_rate"] = (out["reopened_cases"]
                                  / out["total_cases"].replace(0, np.nan)).fillna(0.0)
            out = out.reset_index()

    if dispositions is not None and not dispositions.empty:
        d = dispositions.copy()
        d["month"] = pd.to_datetime(d.get("closure_ts"), errors="coerce").dt.strftime("%Y-%m")
        d = d.dropna(subset=["month"])
        if not d.empty:
            measured = pd.to_numeric(d.get("time_to_close_minutes"), errors="coerce")
            target = pd.to_numeric(d.get("sla_target_minutes"), errors="coerce")
            d["sla_breach"] = ((target > 0) & (measured > target)).astype(int)
            df_part = d.groupby(keys, dropna=False).agg(
                closed_records=("disposition_id", "size"),
                sla_breaches=("sla_breach", "sum")).reset_index()
            df_part["sla_breach_rate"] = (df_part["sla_breaches"]
                                          / df_part["closed_records"].replace(0, np.nan)).fillna(0.0)
            out = df_part if out.empty else out.merge(df_part, on=keys, how="outer")

    if investigations is not None and not investigations.empty:
        i = investigations.copy()
        i["month"] = pd.to_datetime(i.get("ts"), errors="coerce").dt.strftime("%Y-%m")
        i = i.dropna(subset=["month"])
        if not i.empty:
            iv = i.groupby(keys, dropna=False).size().rename("investigation_events").reset_index()
            out = iv if out.empty else out.merge(iv, on=keys, how="outer")

    return out.fillna(0) if not out.empty else out


def monthly_features(enriched_alerts: pd.DataFrame, keys=None) -> pd.DataFrame:
    """Entity x month features - computed from that month's own records.

    The previous build copied entity-level constants into every month, so the
    trend charts and the trend-drift detector were structurally incapable of
    finding a change over time.
    """
    if enriched_alerts is None or enriched_alerts.empty:
        return pd.DataFrame()
    keys = keys or ["entity_id", "month"]
    ordered = enriched_alerts.dropna(subset=["created_dt"]).copy()
    out = alert_features(ordered, keys)
    weekend = weekend_features(ordered, keys)
    if not weekend.empty:
        out = weekend if out.empty else out.merge(weekend, on=keys, how="outer")
    return out
