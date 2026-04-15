"""Aggregate media counts over time for visualization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Sequence, Tuple

from .metadata import MediaMetadata


Granularity = str  # 'hour' | 'day' | 'week' | 'month' | 'year'


@dataclass
class TimelinePoint:
    label: str
    start: datetime
    end: datetime
    count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "count": self.count,
        }


@dataclass
class TimelineReport:
    points: List[TimelinePoint]
    granularity: Granularity
    total_items: int

    def to_dict(self) -> dict[str, object]:
        return {
            "granularity": self.granularity,
            "total_items": self.total_items,
            "points": [point.to_dict() for point in self.points],
        }


class TimelineAnalyzer:
    """Summarize capture dates into time buckets."""

    VALID_GRANULARITIES = {"hour", "day", "week", "month", "year"}

    def __init__(self, granularity: Granularity = "day") -> None:
        if granularity not in self.VALID_GRANULARITIES:
            raise ValueError(f"Granularidad no soportada: {granularity}")
        self.granularity = granularity

    def summarize(self, items: Sequence[MediaMetadata]) -> TimelineReport:
        groups: dict[Tuple[int, ...], Tuple[TimelinePoint, int]] = {}
        for item in items:
            dt = self._normalize_datetime(item.captured_at)
            key, start, end, label = self._bucket(dt)
            current = groups.get(key)
            if current:
                point, count = current
                groups[key] = (point, count + 1)
            else:
                groups[key] = (TimelinePoint(label=label, start=start, end=end, count=0), 1)

        points = []
        for key in sorted(groups.keys()):
            point, count = groups[key]
            point.count = count
            points.append(point)

        return TimelineReport(points=points, granularity=self.granularity, total_items=len(items))

    @staticmethod
    def _normalize_datetime(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _bucket(self, dt: datetime) -> Tuple[Tuple[int, ...], datetime, datetime, str]:
        if self.granularity == "hour":
            start = dt.replace(minute=0, second=0, microsecond=0)
            end = start + timedelta(hours=1)
            label = start.strftime("%Y-%m-%d %H:00")
            key = (start.year, start.month, start.day, start.hour)
            return key, start, end, label
        if self.granularity == "day":
            start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            label = start.strftime("%Y-%m-%d")
            key = (start.year, start.month, start.day)
            return key, start, end, label
        if self.granularity == "week":
            iso_year, iso_week, _ = dt.isocalendar()
            start = datetime.fromisocalendar(iso_year, iso_week, 1).replace(
                tzinfo=dt.tzinfo, hour=0, minute=0, second=0, microsecond=0
            )
            end = start + timedelta(days=7)
            label = f"{iso_year}-W{iso_week:02d}"
            key = (iso_year, iso_week)
            return key, start, end, label
        if self.granularity == "month":
            start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=start.month + 1)
            label = start.strftime("%Y-%m")
            key = (start.year, start.month)
            return key, start, end, label
        # year
        start = dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1)
        label = str(start.year)
        key = (start.year,)
        return key, start, end, label
