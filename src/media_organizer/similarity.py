"""Detect similar or duplicate-looking images using perceptual hashing."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Callable, List, Sequence, Tuple

from PIL import Image, UnidentifiedImageError
import imagehash

from .lens_pairing import deduplicate_assets
from .metadata import MediaMetadata, MediaType

logger = logging.getLogger(__name__)


SimilarityHash = imagehash.ImageHash


@dataclass
class SimilarPair:
    """Pair of visually similar images."""

    first: MediaMetadata
    second: MediaMetadata
    distance: int

    def to_dict(self) -> dict[str, object]:
        return {
            "first": str(self.first.source_path),
            "second": str(self.second.source_path),
            "distance": self.distance,
            "first_captured": self.first.captured_at.isoformat(),
            "second_captured": self.second.captured_at.isoformat(),
        }


@dataclass
class SimilarityReport:
    """Summary of a similarity scan."""

    pairs: List[SimilarPair]
    processed: int
    scanned: int
    skipped: int
    threshold: int

    def to_dict(self) -> dict[str, object]:
        return {
            "threshold": self.threshold,
            "processed": self.processed,
            "scanned": self.scanned,
            "skipped": self.skipped,
            "pairs": [pair.to_dict() for pair in self.pairs],
        }


HashFunction = Callable[[Image.Image], SimilarityHash]


def _resolve_hash_func(method: str, hash_size: int) -> HashFunction:
    if method == "phash":
        return lambda img: imagehash.phash(img, hash_size=hash_size)
    if method == "ahash":
        return lambda img: imagehash.average_hash(img, hash_size=hash_size)
    if method == "dhash":
        return lambda img: imagehash.dhash(img, hash_size=hash_size)
    if method == "whash":
        return lambda img: imagehash.whash(img, hash_size=hash_size)
    raise ValueError(f"Método de hash no soportado: {method}")


class SimilarityAnalyzer:
    """Compute perceptual hashes and look for similar images."""

    def __init__(self, *, threshold: int = 8, hash_size: int = 16, method: str = "phash") -> None:
        if threshold < 0:
            raise ValueError("El umbral de distancia debe ser >= 0.")
        if hash_size <= 0:
            raise ValueError("hash_size debe ser mayor que 0.")
        self.threshold = threshold
        self.hash_size = hash_size
        self.method = method
        self._hash_func = _resolve_hash_func(method, hash_size)

    def analyze(self, items: Sequence[MediaMetadata]) -> SimilarityReport:
        """Return a report with suspected duplicates."""
        items = deduplicate_assets(items)
        scanned = len(items)
        filtered = [item for item in items if item.media_type == MediaType.IMAGE]
        processed = 0
        skipped = scanned - len(filtered)

        pairs: list[SimilarPair] = []
        seen: list[Tuple[MediaMetadata, SimilarityHash]] = []

        for metadata in filtered:
            try:
                phash = self._compute_hash(metadata.source_path)
            except (OSError, UnidentifiedImageError) as exc:
                logger.warning("No se pudo calcular hash para %s: %s", metadata.source_path, exc)
                skipped += 1
                continue

            processed += 1
            for candidate, candidate_hash in seen:
                distance = phash - candidate_hash
                if distance <= self.threshold:
                    pairs.append(SimilarPair(first=candidate, second=metadata, distance=int(distance)))
            seen.append((metadata, phash))

        pairs.sort(key=lambda pair: (pair.distance, pair.first.captured_at, pair.second.captured_at))
        return SimilarityReport(
            pairs=pairs,
            processed=processed,
            scanned=scanned,
            skipped=skipped,
            threshold=self.threshold,
        )

    def _compute_hash(self, path: Path) -> SimilarityHash:
        with Image.open(path) as img:
            img_converted = img.convert("RGB")
            return self._hash_func(img_converted)
