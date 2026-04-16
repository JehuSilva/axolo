"""Clustering utilities for grouping media files into suggested albums."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Iterable, List, Sequence

import numpy as np

from .lens_pairing import deduplicate_assets
from .metadata import MediaCategory, MediaMetadata

logger = logging.getLogger(__name__)

try:  # pragma: no cover - dependencia opcional en tiempo de ejecución
    from sklearn.cluster import DBSCAN
except ImportError:  # pragma: no cover
    DBSCAN = None  # type: ignore


@dataclass(frozen=True)
class ClusterParameters:
    """Parameters used to control the clustering behaviour."""

    time_window_minutes: float = 90.0
    min_samples: int = 3

    def to_dict(self) -> dict[str, float | int]:
        return {
            "time_window_minutes": self.time_window_minutes,
            "min_samples": self.min_samples,
        }


@dataclass
class ClusterResult:
    """Successful cluster group."""

    label: str
    members: List[MediaMetadata]
    start: datetime
    end: datetime
    time_span_minutes: float
    suggested_tags: List[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.members)

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.label,
            "size": self.size,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "time_span_minutes": self.time_span_minutes,
            "suggested_tags": list(self.suggested_tags),
            "members": [
                {
                    "path": str(item.source_path),
                    "captured_at": item.captured_at.isoformat(),
                    "camera_make": item.camera_make,
                    "camera_model": item.camera_model,
                    "timestamp_source": item.timestamp_source.value,
                }
                for item in self.members
            ],
        }


@dataclass
class ClusterSummary:
    """Summary returned by the clustering process."""

    clusters: List[ClusterResult]
    noise: List[MediaMetadata]
    considered_items: int
    total_items: int
    params: ClusterParameters

    def to_dict(self) -> dict:
        return {
            "params": self.params.to_dict(),
            "total_items": self.total_items,
            "considered_items": self.considered_items,
            "clusters": [cluster.to_dict() for cluster in self.clusters],
            "noise": [
                {
                    "path": str(item.source_path),
                    "captured_at": item.captured_at.isoformat(),
                    "camera_make": item.camera_make,
                    "camera_model": item.camera_model,
                    "timestamp_source": item.timestamp_source.value,
                }
                for item in self.noise
            ],
        }


class PhotoClusterer:
    """Run DBSCAN on photo metadata to identify coherent albums."""

    def __init__(self, params: ClusterParameters | None = None) -> None:
        if DBSCAN is None:  # pragma: no cover - requiere dependencia externa
            raise RuntimeError(
                "scikit-learn no está instalado. Instala el extra 'cluster' o agrega scikit-learn como dependencia."
            )
        self.params = params or ClusterParameters()

    def cluster(self, items: Sequence[MediaMetadata]) -> ClusterSummary:
        """Group the provided media metadata into clusters."""
        items = deduplicate_assets(items)
        total_items = len(items)
        filtered: list[MediaMetadata] = [
            item
            for item in items
            if item.category == MediaCategory.PHOTOS_VIDEOS
        ]

        considered_items = len(filtered)

        if not filtered:
            return ClusterSummary(
                clusters=[],
                noise=[],
                considered_items=considered_items,
                total_items=total_items,
                params=self.params,
            )

        features, base_timestamp = self._build_feature_matrix(filtered)
        logger.debug(
            "Generando clusters para %s elementos con base %s y ventana %s minutos",
            considered_items,
            base_timestamp.isoformat(),
            self.params.time_window_minutes,
        )

        model = DBSCAN(
            eps=self.params.time_window_minutes,
            min_samples=self.params.min_samples,
        )
        labels = model.fit_predict(features)

        clusters: list[ClusterResult] = []
        noise: list[MediaMetadata] = []

        unique_labels = sorted(set(int(label) for label in labels if label >= 0))

        for cluster_index, label in enumerate(unique_labels, start=1):
            members = [
                filtered[idx]
                for idx, predicted in enumerate(labels)
                if predicted == label
            ]
            cluster = self._build_cluster_result(cluster_index, members)
            clusters.append(cluster)

        noise = [
            filtered[idx]
            for idx, predicted in enumerate(labels)
            if predicted == -1
        ]

        return ClusterSummary(
            clusters=clusters,
            noise=noise,
            considered_items=considered_items,
            total_items=total_items,
            params=self.params,
        )

    def _build_feature_matrix(
        self, items: Sequence[MediaMetadata]
    ) -> tuple[np.ndarray, datetime]:
        base_timestamp = min(item.captured_at for item in items)
        data = [
            [(item.captured_at - base_timestamp).total_seconds() / 60.0]
            for item in items
        ]
        return np.asarray(data, dtype=float), base_timestamp

    def _build_cluster_result(
        self, index: int, members: Iterable[MediaMetadata]
    ) -> ClusterResult:
        items = sorted(members, key=lambda m: m.captured_at)
        start = items[0].captured_at
        end = items[-1].captured_at
        duration_minutes = (end - start).total_seconds() / 60.0
        tags = self._suggest_tags(items, start, end)

        return ClusterResult(
            label=f"C{index:02d}",
            members=list(items),
            start=start,
            end=end,
            time_span_minutes=duration_minutes,
            suggested_tags=tags,
        )

    @staticmethod
    def _suggest_tags(
        items: Sequence[MediaMetadata],
        start: datetime,
        end: datetime,
    ) -> list[str]:
        tags: list[str] = []

        same_day = start.date() == end.date()
        if same_day:
            tags.append(start.strftime("%Y-%m-%d"))
        else:
            tags.append(f"{start.strftime('%Y-%m-%d')} a {end.strftime('%Y-%m-%d')}")

        camera_models: set[str] = set()
        for item in items:
            if item.camera_model:
                value = item.camera_model.strip()
                if value:
                    camera_models.add(value)

        if len(camera_models) == 1:
            tags.append(next(iter(camera_models)))
        elif camera_models:
            tags.append("varias cámaras")

        if len(items) >= 5:
            tags.append(f"{len(items)} elementos")

        return tags
