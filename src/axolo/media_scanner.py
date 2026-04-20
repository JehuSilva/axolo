"""Directory scanning utilities."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Iterable, Optional, Set


@dataclass
class ScanOptions:
    recursive: bool = True
    follow_symlinks: bool = False
    include_extensions: Optional[Set[str]] = None
    exclude_extensions: Optional[Set[str]] = None
    include_hidden: bool = False


def iter_media_files(source: Path, options: Optional[ScanOptions] = None) -> Generator[Path, None, None]:
    options = options or ScanOptions()
    include = {ext.lower() for ext in options.include_extensions or set()}
    exclude = {ext.lower() for ext in options.exclude_extensions or set()}

    if not source.exists():
        raise FileNotFoundError(f"Source directory {source} does not exist.")

    paths: Iterable[Path]
    if source.is_file():
        paths = [source]
    elif options.recursive:
        paths = source.rglob("*")
    else:
        paths = source.glob("*")

    for path in paths:
        if path.is_dir():
            continue
        if not options.include_hidden and path.name.startswith("."):
            continue
        suffix = path.suffix.lower()
        if include and suffix not in include:
            continue
        if exclude and suffix in exclude:
            continue
        if path.is_symlink() and not options.follow_symlinks:
            continue
        yield path
