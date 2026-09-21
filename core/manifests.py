"""Parse dependency manifests into (ecosystem, name, version) records for vulnerability lookup."""

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Manifest filenames we can parse. Anything else in github_tool._MANIFEST_FILES is reported as unparsed.
PARSERS = {"requirements.txt", "pyproject.toml", "package.json", "go.mod"}

# "exact": version is pinned. "range_floor": lowest version the declared range allows; the installed
# version may be newer, so findings at this basis can be false positives.
EXACT = "exact"
RANGE_FLOOR = "range_floor"

_VERSION = r"v?(\d+(?:\.\d+){0,2}(?:[-+][0-9A-Za-z.\-+]+)?)"
_FLOOR_RE = re.compile(rf"^\s*(?:\^|~>|~=|~|>=|=)?\s*{_VERSION}(?![\w.*])")
_NON_REGISTRY_PREFIXES = ("git", "http", "file:", "link:", "workspace:", "npm:", "github:", "portal:")


@dataclass(frozen=True)
class Dependency:
    ecosystem: str  # OSV ecosystem name: PyPI, npm, Go
    name: str
    version: Optional[str]  # None when it can't be resolved to something checkable
    basis: Optional[str]  # EXACT or RANGE_FLOOR; None when version is None
    source: str  # manifest filename
    reason: str = ""  # why version is None


def _pad(version: str) -> str:
    """Pad partial semver ('1.2') to three components ('1.2.0') for npm."""
    core, rest = re.match(r"^(\d+(?:\.\d+)*)(.*)$", version).groups()
    parts = core.split(".")
    while len(parts) < 3:
        parts.append("0")
    return ".".join(parts) + rest


def _floor(spec: str) -> Optional[str]:
    """Lowest version a simple range like '^1.2.3', '>=1.0', '~1.4' allows, or None if ambiguous."""
    spec = spec.strip()
    if not spec or "||" in spec or spec in ("*", "latest", "next"):
        return None
    # Compound like ">=1.2.3 <2.0.0": the floor is the leading >= comparator.
    first = re.split(r"[\s,]+", spec)[0]
    if first.startswith(("<", ">")) and not first.startswith(">="):
        return None
    m = _FLOOR_RE.match(first)
    return m.group(1) if m else None


def parse_requirements(text: str, source: str = "requirements.txt") -> list[Dependency]:
    deps = []
    for raw in text.splitlines():
        line = raw.split(" #")[0].strip()
        if not line or line.startswith(("#", "-")) or "://" in line or line.startswith("git+"):
            continue
        line = line.split(";")[0].strip()  # environment markers
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$", line)
        if not m:
            continue
        name = re.sub(r"[-_.]+", "-", m.group(1)).lower()
        spec = m.group(2).strip()

        pinned = re.match(r"^===?\s*([0-9][^\s,*]*)$", spec)
        if pinned:
            deps.append(Dependency("PyPI", name, pinned.group(1), EXACT, source))
            continue
        floor = _floor(spec) if spec.startswith((">=", "~=")) else None
        if floor:
            deps.append(Dependency("PyPI", name, floor, RANGE_FLOOR, source))
        else:
            deps.append(Dependency("PyPI", name, None, None, source, "no resolvable version"))
    return deps


def parse_pyproject(text: str, source: str = "pyproject.toml") -> list[Dependency]:
    """PEP 621 [project] dependencies and optional-dependencies. Poetry-only layouts are unsupported."""
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        raise ValueError("pyproject.toml parsing needs Python 3.11+")
    data = tomllib.loads(text)
    project = data.get("project")
    if project is None:
        if data.get("tool", {}).get("poetry"):
            raise ValueError("Poetry dependency layout is not supported")
        return []
    lines = list(project.get("dependencies", []))
    for group in (project.get("optional-dependencies") or {}).values():
        lines += group
    return parse_requirements("\n".join(lines), source)


def parse_package_json(text: str, source: str = "package.json") -> list[Dependency]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("package.json is not an object")
    deps = []
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, spec in (data.get(section) or {}).items():
            spec = str(spec).strip()
            if spec.startswith(_NON_REGISTRY_PREFIXES):
                deps.append(Dependency("npm", name, None, None, source, "non-registry source"))
                continue
            full = re.fullmatch(r"=?\s*v?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-+]+)?)", spec)
            if full:  # a partial version like "1.2" is a range in npm, so it falls through to the floor
                deps.append(Dependency("npm", name, full.group(1), EXACT, source))
                continue
            floor = _floor(spec)
            if floor:
                deps.append(Dependency("npm", name, _pad(floor), RANGE_FLOOR, source))
            else:
                deps.append(Dependency("npm", name, None, None, source, "no resolvable version"))
    return deps


def parse_go_mod(text: str, source: str = "go.mod") -> list[Dependency]:
    deps = []
    in_block = False
    for raw in text.splitlines():
        line = raw.split("//")[0].strip()
        if not line:
            continue
        if in_block:
            if line == ")":
                in_block = False
                continue
            body = line
        elif line.startswith("require ("):
            in_block = True
            continue
        elif line.startswith("require "):
            body = line[len("require "):].strip()
        else:
            continue
        parts = body.split()
        if len(parts) >= 2 and re.match(r"^v\d", parts[1]):
            deps.append(Dependency("Go", parts[0], parts[1].lstrip("v"), EXACT, source))
    return deps


def parse_manifests(manifests: dict[str, str]) -> tuple[list[Dependency], list[str]]:
    """
    Parse every supported manifest.

    Returns (dependencies, unparsed) where unparsed lists manifest files that were present but
    couldn't be parsed (unsupported format, or malformed content), so callers can report the gap.
    """
    deps: list[Dependency] = []
    unparsed: list[str] = []
    for name, text in manifests.items():
        try:
            if name == "requirements.txt":
                deps += parse_requirements(text, name)
            elif name == "pyproject.toml":
                deps += parse_pyproject(text, name)
            elif name == "package.json":
                deps += parse_package_json(text, name)
            elif name == "go.mod":
                deps += parse_go_mod(text, name)
            else:
                unparsed.append(name)
        except Exception as e:
            logger.warning(f"Failed to parse {name}: {e}")
            unparsed.append(name)
    return deps, unparsed
