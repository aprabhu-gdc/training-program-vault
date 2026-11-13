"""Dashboard figures computed from the two analytics lists.

Mirrors the Power BI semantic model (``powerbi/TrainingBotAnalytics``) so the
Claude dashboard and the Power BI report agree during the switchover:

* Questions Asked   = DISTINCTCOUNT(QueryEvents[RequestId])
* Unknown Questions = Questions Asked where IsUnknown
* Concept bar chart = COUNTROWS(QueryEvents) by Concept, Unknown excluded
* Feedback Count / Helpful Count / Inaccurate Count / Helpful %
* Rating donut, feedback table, and the Date and User slicers

The Date filter is inclusive and in UTC, matching the model's UTC timestamps.
Users are the union of both lists, like the model's ``DimUser``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Iterable, Mapping

from teams_bot.services.analytics import UNKNOWN_CONCEPT


QUERY_FIELDS = ("Title", "Timestamp", "RequestId", "UserId", "UserName", "Concept", "IsUnknown")
FEEDBACK_FIELDS = ("Timestamp", "RequestId", "UserId", "UserName", "Rating", "Comment", "Concepts")

MAX_FEEDBACK_ROWS = 200


@dataclass(frozen=True)
class ReportFilter:
    start_date: date | None = None
    end_date: date | None = None
    user_id: str | None = None


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("true", "1", "yes")


def _in_filter(row: Mapping[str, Any], stamp: datetime | None, flt: ReportFilter) -> bool:
    if flt.user_id and str(row.get("UserId") or "") != flt.user_id:
        return False
    if flt.start_date or flt.end_date:
        if stamp is None:
            return False
        day = stamp.astimezone(UTC).date()
        if flt.start_date and day < flt.start_date:
            return False
        if flt.end_date and day > flt.end_date:
            return False
    return True


def build_report(
    query_rows: Iterable[Mapping[str, Any]],
    feedback_rows: Iterable[Mapping[str, Any]],
    flt: ReportFilter,
    *,
    truncated: bool = False,
) -> dict[str, Any]:
    # The user slicer lists everyone active in the date range, even while one
    # user is selected, so users are collected before the user filter applies.
    date_only = ReportFilter(flt.start_date, flt.end_date, None)
    users: dict[str, dict[str, Any]] = {}

    def collect(rows: Iterable[Mapping[str, Any]], *, is_query: bool) -> list[tuple[Mapping[str, Any], datetime | None]]:
        kept = []
        for row in rows:
            stamp = _parse_timestamp(row.get("Timestamp"))
            if not _in_filter(row, stamp, date_only):
                continue
            user_id = str(row.get("UserId") or "")
            if user_id:
                entry = users.setdefault(
                    user_id,
                    {"user_id": user_id, "user_name": str(row.get("UserName") or user_id), "questions": 0},
                )
                if is_query:
                    entry["questions"] += 1
            if _in_filter(row, stamp, flt):
                kept.append((row, stamp))
        return kept

    queries = collect(query_rows, is_query=True)
    feedback = collect(feedback_rows, is_query=False)

    request_ids = {str(row.get("RequestId") or "") for row, _ in queries}
    unknown_ids = {
        str(row.get("RequestId") or "")
        for row, _ in queries
        if _as_bool(row.get("IsUnknown")) or row.get("Concept") == UNKNOWN_CONCEPT
    }

    concept_counts = Counter(
        str(row.get("Concept") or "")
        for row, _ in queries
        if not (_as_bool(row.get("IsUnknown")) or row.get("Concept") == UNKNOWN_CONCEPT)
        and row.get("Concept")
    )

    ratings = Counter(str(row.get("Rating") or "").lower() for row, _ in feedback)
    feedback_count = len(feedback)
    helpful = ratings.get("helpful", 0)
    inaccurate = ratings.get("inaccurate", 0)

    daily: dict[str, dict[str, int]] = {}
    for row, stamp in queries:
        if stamp is None:
            continue
        bucket = daily.setdefault(stamp.astimezone(UTC).date().isoformat(), {"questions": 0, "unknown": 0})
        bucket["questions"] += 1
        if str(row.get("RequestId") or "") in unknown_ids:
            bucket["unknown"] += 1

    feedback_table = [
        {
            "timestamp": stamp.isoformat().replace("+00:00", "Z") if stamp else "",
            "user_name": str(row.get("UserName") or ""),
            "rating": str(row.get("Rating") or ""),
            "concepts": str(row.get("Concepts") or ""),
            "comment": str(row.get("Comment") or ""),
        }
        for row, stamp in sorted(
            feedback, key=lambda item: item[1] or datetime.min.replace(tzinfo=UTC), reverse=True
        )[:MAX_FEEDBACK_ROWS]
    ]

    return {
        "filter": {
            "start_date": flt.start_date.isoformat() if flt.start_date else None,
            "end_date": flt.end_date.isoformat() if flt.end_date else None,
            "user_id": flt.user_id,
            "timezone": "UTC",
        },
        "totals": {
            "questions_asked": len(request_ids - {""}),
            "unknown_questions": len(unknown_ids - {""}),
            "feedback_count": feedback_count,
            "helpful_count": helpful,
            "inaccurate_count": inaccurate,
            "helpful_pct": round(helpful / feedback_count, 4) if feedback_count else None,
        },
        "concepts": [
            {"concept": concept, "questions": count}
            for concept, count in sorted(concept_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "ratings": [
            {"rating": rating, "count": count}
            for rating, count in sorted(ratings.items(), key=lambda kv: (-kv[1], kv[0]))
            if rating
        ],
        "daily": [{"date": day, **counts} for day, counts in sorted(daily.items())],
        "users": sorted(users.values(), key=lambda u: (-u["questions"], u["user_name"].lower())),
        "feedback": feedback_table,
        "feedback_rows_shown": len(feedback_table),
        "source_truncated": truncated,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
