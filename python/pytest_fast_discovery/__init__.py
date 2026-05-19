from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pytest_fast_discovery import _native


@dataclass(frozen=True)
class DiscoveryResult:
    nodeids: list[str]
    files: list[str]

    @property
    def files_seen(self) -> int:
        return len(self.files)


def discover_paths(
    paths: Iterable[Path | str],
    *,
    root: Path | str,
    python_files: Iterable[str],
    python_classes: Iterable[str],
    python_functions: Iterable[str],
    norecursedirs: Iterable[str],
) -> DiscoveryResult:
    nodeids, files = _native.discover(
        [str(path) for path in paths],
        str(root),
        list(python_files),
        list(python_classes),
        list(python_functions),
        list(norecursedirs),
    )

    return DiscoveryResult(nodeids=nodeids, files=files)
