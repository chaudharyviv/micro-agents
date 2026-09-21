"""Tests for core/manifests.py."""

import json

import pytest

from core.manifests import (
    EXACT,
    RANGE_FLOOR,
    parse_go_mod,
    parse_manifests,
    parse_package_json,
    parse_pyproject,
    parse_requirements,
)


def by_name(deps):
    return {d.name: d for d in deps}


class TestRequirements:
    def test_pinned_and_normalized(self):
        deps = by_name(parse_requirements("Flask==2.0.1\nPyYAML_x==5.1 # comment\n"))
        assert (deps["flask"].version, deps["flask"].basis, deps["flask"].ecosystem) == ("2.0.1", EXACT, "PyPI")
        assert deps["pyyaml-x"].version == "5.1"

    def test_extras_and_markers(self):
        deps = by_name(parse_requirements('requests[security]==2.25.0 ; python_version < "3.9"\n'))
        assert deps["requests"].version == "2.25.0"

    def test_ranges(self):
        deps = by_name(parse_requirements("a>=1.2\nb~=2.0.1\nc\nd<3\ne==1.*\n"))
        assert (deps["a"].version, deps["a"].basis) == ("1.2", RANGE_FLOOR)
        assert (deps["b"].version, deps["b"].basis) == ("2.0.1", RANGE_FLOOR)
        assert deps["c"].version is None
        assert deps["d"].version is None
        assert deps["e"].version is None

    def test_skips_options_urls_and_blank(self):
        text = "-r other.txt\n-e .\n--index-url https://x\ngit+https://github.com/a/b.git\n\n# just a comment\n"
        assert parse_requirements(text) == []


class TestPackageJson:
    def test_sections_and_specs(self):
        pkg = {
            "dependencies": {"lodash": "4.17.15", "express": "^4.17.1", "react": "~17.0", "old": "*"},
            "devDependencies": {"jest": ">=27.0.0 <29", "gitdep": "github:a/b", "lt": "<2"},
        }
        deps = by_name(parse_package_json(json.dumps(pkg)))
        assert (deps["lodash"].version, deps["lodash"].basis) == ("4.17.15", EXACT)
        assert (deps["express"].version, deps["express"].basis) == ("4.17.1", RANGE_FLOOR)
        assert (deps["react"].version, deps["react"].basis) == ("17.0.0", RANGE_FLOOR)  # padded, and a range
        assert (deps["jest"].version, deps["jest"].basis) == ("27.0.0", RANGE_FLOOR)
        assert deps["old"].version is None
        assert deps["gitdep"].version is None and "non-registry" in deps["gitdep"].reason
        assert deps["lt"].version is None
        assert all(d.ecosystem == "npm" for d in deps.values())

    def test_or_ranges_are_unresolved(self):
        deps = parse_package_json(json.dumps({"dependencies": {"x": "^1.0.0 || ^2.0.0"}}))
        assert deps[0].version is None


class TestGoMod:
    def test_block_and_single_require(self):
        text = """module example.com/m

go 1.20

require github.com/gin-gonic/gin v1.6.0

require (
\tgolang.org/x/text v0.3.0 // indirect
\tgithub.com/pkg/errors v0.9.1
)

replace github.com/a/b => ../b
"""
        deps = by_name(parse_go_mod(text))
        assert deps["github.com/gin-gonic/gin"].version == "1.6.0"
        assert deps["golang.org/x/text"].version == "0.3.0"
        assert deps["github.com/pkg/errors"].version == "0.9.1"
        assert "github.com/a/b" not in deps
        assert all(d.ecosystem == "Go" and d.basis == EXACT for d in deps.values())


class TestPyproject:
    def test_pep621_dependencies(self):
        pytest.importorskip("tomllib")
        text = """
[project]
name = "x"
dependencies = ["httpx>=0.27,<1", "pydantic==2.5.0", "anyio"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]
"""
        deps = by_name(parse_pyproject(text))
        assert (deps["httpx"].version, deps["httpx"].basis) == ("0.27", RANGE_FLOOR)
        assert (deps["pydantic"].version, deps["pydantic"].basis) == ("2.5.0", EXACT)
        assert deps["anyio"].version is None
        assert deps["pytest"].version == "8.0"

    def test_poetry_layout_is_unsupported(self):
        pytest.importorskip("tomllib")
        with pytest.raises(ValueError):
            parse_pyproject('[tool.poetry.dependencies]\nrequests = "^2.0"\n')
        _, unparsed = parse_manifests({"pyproject.toml": '[tool.poetry.dependencies]\nrequests = "^2.0"\n'})
        assert unparsed == ["pyproject.toml"]


class TestParseManifests:
    def test_reports_unsupported_and_malformed(self):
        deps, unparsed = parse_manifests(
            {"requirements.txt": "flask==2.0.1", "Cargo.toml": "[package]", "package.json": "{not json"}
        )
        assert [d.name for d in deps] == ["flask"]
        assert sorted(unparsed) == ["Cargo.toml", "package.json"]
