from __future__ import annotations

from pathlib import Path

import pytest

from pytest_fast_discovery import discover_paths


def write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def test_discovers_pytest_nodeids_without_importing_modules(
    tmp_path: Path,
) -> None:
    write(
        tmp_path / "account" / "tests" / "test_views.py",
        '''
RAISE_IF_IMPORTED = 1 / 0


def test_module_function():
    pass


async def test_async_module_function():
    pass


def helper_function():
    pass


class TestTokenAuthViews:
    def test_login(self):
        pass

    async def test_async_login(self):
        pass

    def helper_method(self):
        pass


class PointsCategoryViewSetTests:
    def test_list(self):
        pass


class Helper:
    def test_not_collected(self):
        pass
''',
    )
    write(
        tmp_path / "account" / "migrations" / "test_ignored.py",
        "def test_migration_file(): pass\n",
    )
    write(
        tmp_path / "account" / "tests" / "helpers.py",
        "def test_helper_module_is_not_matched(): pass\n",
    )

    result = discover_paths(
        [tmp_path],
        root=tmp_path,
        python_files=["test_*.py", "*_test.py", "tests.py"],
        python_classes=["Test*", "*Tests"],
        python_functions=["test_*"],
        norecursedirs=["migrations"],
    )

    assert result.nodeids == [
        "account/tests/test_views.py::test_module_function",
        "account/tests/test_views.py::test_async_module_function",
        "account/tests/test_views.py::TestTokenAuthViews::test_login",
        "account/tests/test_views.py::TestTokenAuthViews::test_async_login",
        "account/tests/test_views.py::PointsCategoryViewSetTests::test_list",
    ]
    assert result.files_seen == 1


def test_discovers_explicit_test_file_even_when_pattern_does_not_match(
    tmp_path: Path,
) -> None:
    write(
        tmp_path / "account" / "tests" / "views_spec.py",
        '''
class TestExplicitFile:
    def test_it_is_collected(self):
        pass
''',
    )

    result = discover_paths(
        [tmp_path / "account" / "tests" / "views_spec.py"],
        root=tmp_path,
        python_files=["test_*.py"],
        python_classes=["Test*"],
        python_functions=["test_*"],
        norecursedirs=[],
    )

    assert result.nodeids == [
        "account/tests/views_spec.py::TestExplicitFile::test_it_is_collected",
    ]


def test_discovers_unittest_style_classes_that_do_not_match_class_patterns(
    tmp_path: Path,
) -> None:
    write(
        tmp_path / "account" / "tests" / "test_schema.py",
        '''
from django.test import TestCase


class UserReferralTypeTest(TestCase):
    def test_user_referral_object_type(self):
        pass
''',
    )

    result = discover_paths(
        [tmp_path],
        root=tmp_path,
        python_files=["test_*.py"],
        python_classes=["Test*", "*Tests"],
        python_functions=["test_*"],
        norecursedirs=[],
    )

    assert result.nodeids == [
        "account/tests/test_schema.py::UserReferralTypeTest::test_user_referral_object_type",
    ]


def test_multiline_string_contents_do_not_end_class_scope(
    tmp_path: Path,
) -> None:
    write(
        tmp_path / "phone2action" / "tests" / "test_tasks.py",
        '''class TestImportAdvocates(TestCase):
    def setUpTestData(cls):
        csv_content = b""""Header"
unindented,csv,row
"""

    def test_users_created(self):
        pass
''',
    )

    result = discover_paths(
        [tmp_path],
        root=tmp_path,
        python_files=["test_*.py"],
        python_classes=["Test*"],
        python_functions=["test_*"],
        norecursedirs=[],
    )

    assert result.nodeids == [
        "phone2action/tests/test_tasks.py::TestImportAdvocates::test_users_created",
    ]


def test_fast_discover_collect_only_prints_nodeids(pytester: pytest.Pytester) -> None:
    pytester.makepyprojecttoml(
        """
        [tool.pytest.ini_options]
        python_files = ["test_*.py"]
        python_classes = ["Test*"]
        python_functions = ["test_*"]
        norecursedirs = ["migrations"]
        """
    )
    pytester.makepyfile(
        test_sample="""
        def test_function():
            pass

        class TestSample:
            def test_method(self):
                pass
        """,
    )

    result = pytester.runpytest("--fast-discover", "--collect-only", "-q")

    assert result.ret == 0
    result.stdout.fnmatch_lines(
        [
            "test_sample.py::test_function",
            "test_sample.py::TestSample::test_method",
        ],
    )
    result.stderr.fnmatch_lines(["fast-discovery: 2 tests from 1 files in *s"])


def test_fast_discover_collectonly_alias(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        test_sample="""
        def test_function():
            pass
        """,
    )

    result = pytester.runpytest("--fastdiscover", "--collectonly", "-q")

    assert result.ret == 0
    result.stdout.fnmatch_lines(["test_sample.py::test_function"])
    result.stderr.fnmatch_lines(["fast-discovery: 1 tests from 1 files in *s"])


def test_fast_discover_runs_tests_with_pytest(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        test_sample="""
        def test_function():
            assert True

        class TestSample:
            def test_method(self):
                assert True
        """,
    )

    result = pytester.runpytest("--fast-discover", "-q")

    assert result.ret == 0
    result.stdout.fnmatch_lines(["..*", "2 passed in *s"])
    result.stderr.fnmatch_lines(["fast-discovery: 2 tests from 1 files in *s"])


def test_fast_discover_run_mode_preserves_explicit_nodeid(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_sample="""
        def test_selected():
            assert True

        def test_other():
            raise AssertionError("should not run")
        """,
    )

    result = pytester.runpytest(
        "--fast-discover",
        "test_sample.py::test_selected",
        "-q",
    )

    assert result.ret == 0
    result.stdout.fnmatch_lines([".*", "1 passed in *s"])
    result.stdout.no_fnmatch_line("*should not run*")
