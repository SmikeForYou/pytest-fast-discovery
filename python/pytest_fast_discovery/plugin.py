from __future__ import annotations

import os
import sys
from pathlib import Path
from time import perf_counter

import pytest
from pytest import Config, ExitCode, Parser

from pytest_fast_discovery import DiscoveryResult, discover_paths

FAST_DISCOVER_OPTIONS = (
    "--fast-discover",
    "--fastdiscover",
    "--fast-discovery",
)
COLLECT_ONLY_OPTIONS = ("--collect-only", "--collectonly", "--co")


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("fast-discovery")
    group.addoption(
        *FAST_DISCOVER_OPTIONS,
        dest="fast_discover",
        action="store_true",
        help="Use Rust source scanning before pytest collection.",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(
    early_config: Config,
    parser: Parser,
    args: list[str],
) -> None:
    if not _has_option(args, FAST_DISCOVER_OPTIONS):
        return
    if not _has_option(args, COLLECT_ONLY_OPTIONS):
        return

    early_config.inicfg["DJANGO_SETTINGS_MODULE"] = ""
    early_config._inicache["DJANGO_SETTINGS_MODULE"] = ""


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config: Config) -> ExitCode | None:
    if not config.getoption("fast_discover"):
        return None

    result, duration = _discover(config)

    if config.getoption("collectonly"):
        _write_nodeids(result.nodeids)
        _write_summary(result, duration)
        return ExitCode.OK

    runtime_args = _runtime_args(config, result)
    if not _is_xdist_worker(config):
        _write_summary(result, duration)

    if not runtime_args:
        return ExitCode.NO_TESTS_COLLECTED

    config.args = runtime_args
    return None


def _discover(config: Config) -> tuple[DiscoveryResult, float]:
    root = config.rootpath
    paths = config.args or [str(root)]
    started_at = perf_counter()
    result = discover_paths(
        paths,
        root=root,
        python_files=config.getini("python_files"),
        python_classes=config.getini("python_classes"),
        python_functions=config.getini("python_functions"),
        norecursedirs=config.getini("norecursedirs"),
    )
    duration = perf_counter() - started_at

    return result, duration


def _write_nodeids(nodeids: list[str]) -> None:
    for nodeid in nodeids:
        sys.stdout.write(f"{nodeid}\n")


def _write_summary(result: DiscoveryResult, duration: float) -> None:
    sys.stderr.write(
        f"fast-discovery: {len(result.nodeids)} tests from "
        f"{result.files_seen} files in {duration:.2f}s\n",
    )


def _is_xdist_worker(config: Config) -> bool:
    return hasattr(config, "workerinput") or os.environ.get("PYTEST_XDIST_WORKER", "") not in (
        "",
        "master",
    )


def _runtime_args(config: Config, result: DiscoveryResult) -> list[str]:
    explicit_prefixes = [
        prefix
        for prefix in (
            _nodeid_prefix(arg, config.rootpath)
            for arg in config.args
            if "::" in arg
        )
        if prefix is not None
    ]

    if explicit_prefixes:
        return [
            nodeid
            for nodeid in result.nodeids
            if any(
                nodeid == prefix or nodeid.startswith(f"{prefix}::")
                for prefix in explicit_prefixes
            )
        ]

    return result.files


def _nodeid_prefix(arg: str, root: Path) -> str | None:
    path_part, *selectors = arg.split("::")
    path = Path(path_part)

    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError:
            return None

    prefix = path.as_posix()

    if selectors:
        prefix = f"{prefix}::{'::'.join(selectors)}"

    return prefix


def _has_option(args: list[str], options: tuple[str, ...]) -> bool:
    return any(arg in options for arg in args)
