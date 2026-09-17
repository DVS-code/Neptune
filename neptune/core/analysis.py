"""Evidence-based tuning suggestions derived from stored run analysis."""

from __future__ import annotations

from neptune.core.logs import MAP_COLUMNS, boost_map_path, map_context
from neptune.core.models import NeptuneLog, TuneChange, TuneSuggestion, finite_int

HIGH_SPEED_GOALS = (
    "Faster 60-130",
    "Faster 100-200",
    "Faster 150-250",
    "More top-end power",
    "Faster high-speed acceleration",
)
GOALS = ("", *HIGH_SPEED_GOALS, "Better shift recovery")


def _section(tune_state: dict | None, name: str) -> dict:
    value = (tune_state or {}).get(name)
    return value if isinstance(value, dict) else {}


def _map_proposal(
    tune_state: dict | None, rows: int, cells: set[tuple[int, int]], factor: float, title: str
) -> tuple[list[TuneChange], list[dict]]:
    turbo = _section(tune_state, "turbo")
    points = turbo.get("map_points")
    if not isinstance(points, list) or not points:
        return [TuneChange("boost_map", title, "unavailable", "inspect manually")], []
    rows = max(1, finite_int(turbo.get("map_rows"), rows) or rows)
    proposed = list(points)
    touched = 0
    for row, column in sorted(cells):
        index = row * MAP_COLUMNS + column
        if not 0 <= index < len(proposed):
            continue
        try:
            proposed[index] = max(0.0, min(4.0, float(proposed[index]) * factor))
            touched += 1
        except (TypeError, ValueError, OverflowError):
            continue
    if not touched:
        return [TuneChange("boost_map", title, "no matching cells", "inspect manually")], []
    change = TuneChange(
        "boost_map",
        title,
        f"{touched} touched cell{'s' if touched != 1 else ''}",
        f"{factor * 100.0 - 100.0:+.0f}% in selected cells",
    )
    return [change], [{"turbo": {"map_points": proposed, "map_rows": rows}}]


def _shift_proposal(tune_state: dict | None, event) -> tuple[list[TuneChange], list[dict]]:
    unavailable = [TuneChange("transmission", event.label, "unavailable", "inspect manually")], []
    tune = _section(_section(tune_state, "transmission"), "transmission")
    ratios = tune.get("ratios")
    to_gear = event.details.get("to_gear")
    if not isinstance(ratios, list) or not isinstance(to_gear, int) or not 1 <= to_gear <= len(ratios):
        return unavailable
    index = to_gear - 1
    try:
        current = float(ratios[index])
    except (TypeError, ValueError, OverflowError):
        return unavailable
    proposed = list(ratios)
    proposed[index] = max(0.05, min(10.0, current * 1.03))
    change = TuneChange("transmission", f"Gear {to_gear} ratio", f"{current:.2f}", f"{proposed[index]:.2f}")
    return [change], [{"transmission": {"transmission": {"ratios": proposed}}}]


def suggestions_for(log: NeptuneLog, goal: str = "", tune_state: dict | None = None) -> list[TuneSuggestion]:
    """Create conservative suggestions that cite observed log evidence.

    Suggestions are proposals only. The UI must preview one and explicitly create a new tune
    revision before any proposal can be applied to saved or live state.
    """
    analysis = log.analysis
    if analysis.quality in ("Invalid", "Poor"):
        return [
            TuneSuggestion(
                goal or "Valid run first",
                "Run quality gate",
                "Do not auto-tune from this run yet.",
                f"The captured run is {analysis.quality.lower()}: " + "; ".join(analysis.reasons),
            )
        ]

    suggestions: list[TuneSuggestion] = []
    for event in analysis.events:
        if event.kind != "shift":
            continue
        before = event.details.get("rpm_before")
        after = event.details.get("rpm_after")
        if before and after and after < before * 0.70:
            changes, patches = _shift_proposal(tune_state, event)
            suggestions.append(
                TuneSuggestion(
                    goal or "Better shift recovery",
                    event.label,
                    "Review the next gear ratio or final drive.",
                    f"RPM fell from {before:.0f} to {after:.0f} on the logged shift, below the conservative "
                    "70% landing threshold.",
                    changes,
                    event.timestamp,
                    proposal_patches=patches,
                )
            )

    if goal in HIGH_SPEED_GOALS and "peak_boost" in analysis.metrics:
        max_rpm, rows = map_context(log)
        hits, _ = boost_map_path(log.samples, max_rpm, rows)
        cells = {(row, column) for row, column in hits if column >= 7}
        changes, patches = _map_proposal(tune_state, rows, cells, 1.05, "High-RPM boost")
        suggestions.append(
            TuneSuggestion(
                goal,
                "High-RPM boost review",
                "Compare the high-RPM map cells against a clean run before raising them.",
                f"This run recorded peak boost of {analysis.metrics['peak_boost']:.1f}; the recommendation is "
                "tied to the captured turbo channel, not a generic boost rule.",
                changes,
                proposal_patches=patches,
            )
        )
    return suggestions
