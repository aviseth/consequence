"""What is allowed, expressed as data rather than as a wall of if statements.

Two decisions shape everything here.

*Deny wins.* A path that matches both an allow and a deny is denied. Any other
ordering means the safety of a policy depends on the order somebody happened to
write the rules in, and nobody can review that.

*The default is a choice you have to make.* There is no implicit stance. A policy
either says ``default = "allow"``, which is a monitoring posture, or
``default = "deny"``, which is a containment one. Guessing on the reader's behalf
is how a policy ends up meaning something nobody intended.
"""

from __future__ import annotations

import fnmatch
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib  # type: ignore[import-not-found, unused-ignore]

from consequence.effects import (
    DB_MUTATE,
    DB_QUERY,
    DB_SCHEMA,
    DIR_CREATE,
    DIR_DELETE,
    ENV_CHANGE,
    FILE_APPEND,
    FILE_COPY,
    FILE_DELETE,
    FILE_LINK,
    FILE_MOVE,
    FILE_READ,
    FILE_WRITE,
    NET_CONNECT,
    NET_REQUEST,
    PERM_CHANGE,
    PROCESS_SIGNAL,
    PROCESS_SPAWN,
    Effect,
)

ALLOW = "allow"
DENY = "deny"

#: Which policy section governs which kind of effect.
DOMAIN: dict[str, str] = {
    FILE_READ: "filesystem",
    FILE_WRITE: "filesystem",
    FILE_APPEND: "filesystem",
    FILE_DELETE: "filesystem",
    FILE_MOVE: "filesystem",
    FILE_COPY: "filesystem",
    FILE_LINK: "filesystem",
    DIR_CREATE: "filesystem",
    DIR_DELETE: "filesystem",
    PERM_CHANGE: "filesystem",
    PROCESS_SPAWN: "process",
    PROCESS_SIGNAL: "process",
    NET_CONNECT: "network",
    NET_REQUEST: "network",
    DB_QUERY: "database",
    DB_MUTATE: "database",
    DB_SCHEMA: "database",
    ENV_CHANGE: "environment",
}

#: Filesystem verbs, so a policy can allow reading everywhere and writing in one
#: directory without listing every kind by hand.
FILE_VERB: dict[str, str] = {
    FILE_READ: "read",
    FILE_WRITE: "write",
    FILE_APPEND: "write",
    FILE_COPY: "write",
    FILE_LINK: "write",
    DIR_CREATE: "write",
    FILE_MOVE: "write",
    PERM_CHANGE: "write",
    FILE_DELETE: "delete",
    DIR_DELETE: "delete",
}


class PolicyError(ValueError):
    """A policy file that cannot be understood."""


@dataclass
class Decision:
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


@dataclass
class Policy:
    """A set of rules, and a stance for anything they do not mention."""

    default: str = ALLOW
    filesystem: dict[str, list[str]] = field(default_factory=dict)
    process: dict[str, list[str]] = field(default_factory=dict)
    network: dict[str, list[str]] = field(default_factory=dict)
    database: dict[str, list[str]] = field(default_factory=dict)
    environment: dict[str, list[str]] = field(default_factory=dict)
    allow_schema_changes: bool = True
    source: Path | None = None

    # --- the one method that matters ------------------------------------------

    def decide(self, effect: Effect) -> Decision:
        """Allow or deny one effect, with a reason a person can act on."""
        domain = DOMAIN.get(effect.kind, "")
        rules = getattr(self, domain, {}) if domain else {}

        # Deny first, always, whatever else matches.
        for pattern in rules.get("deny", []):
            if self._matches(effect, pattern):
                return Decision(False, f"denied by {domain}.deny pattern {pattern!r}")

        if effect.kind == DB_SCHEMA and not self.allow_schema_changes:
            return Decision(False, "schema changes are switched off by policy")

        allowed_patterns = self._allow_patterns(effect, rules)
        if allowed_patterns is not None:
            for pattern in allowed_patterns:
                if self._matches(effect, pattern):
                    return Decision(True, f"allowed by {domain} pattern {pattern!r}")
            return Decision(
                False,
                f"no {domain} rule allows this"
                + (f" ({effect.severity.label})" if effect.destructive else ""),
            )

        if self.default == ALLOW:
            return Decision(True, "")
        return Decision(False, f"nothing in the policy allows {effect.kind}")

    def _allow_patterns(self, effect: Effect, rules: dict[str, list[str]]) -> list[str] | None:
        """Patterns that could allow this effect, or None if the policy is silent."""
        verb = FILE_VERB.get(effect.kind)
        if verb is not None and verb in rules:
            return rules[verb]
        if "allow" in rules:
            return rules["allow"]
        return None

    def _matches(self, effect: Effect, pattern: str) -> bool:
        """Does one rule cover this effect?

        A filesystem rule is a path glob. Everything else is matched against the
        target and against the detail, because both spellings are natural to
        write: ``git`` names the program, ``git status*`` names the command, and
        a policy author should not have to know which one the interceptor
        recorded where.
        """
        if DOMAIN.get(effect.kind) == "filesystem":
            return _path_matches(effect.target, pattern)
        candidates = {effect.target}
        if effect.detail:
            candidates.add(effect.detail)
            candidates.add(f"{effect.target} {effect.detail}")
        return any(fnmatch.fnmatch(candidate, pattern) for candidate in candidates)


def _path_matches(target: str, pattern: str) -> bool:
    """Glob a path, with ``**`` meaning what people expect it to mean.

    Two things this gets right that a plain ``fnmatch`` does not.

    ``*`` does not cross a separator. Otherwise ``/etc/*`` matches
    ``/etc/nginx/nginx.conf`` and a policy is far more permissive than it reads.
    ``**`` is the one that spans directories.

    The literal part of the pattern is resolved the same way the target is.
    On macOS ``/tmp`` is a symlink to ``/private/tmp``, so a rule written as
    ``/tmp/**`` would otherwise match nothing at all, which is the worst kind of
    policy bug: it fails open on the allow list and silently.
    """
    return _glob(_slashes(_resolve(target)), _slashes(_resolve_pattern(pattern)))


def _slashes(path: str) -> str:
    """One separator, so the globber has one thing to reason about.

    Policies are written with forward slashes whatever the platform, because
    that is what a TOML file full of paths looks like everywhere. Windows then
    hands back native separators from resolution, and a pattern ends up spelled
    two ways in the same string.
    """
    return path.replace("\\", "/") if os.sep == "\\" else path


def _resolve(path: str) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except (OSError, RuntimeError):  # pragma: no cover - unresolvable paths are rare
        return str(path)


#: Where a pattern stops being a literal path and starts being a glob.
_GLOB_CHARS = "*?["


def _resolve_pattern(pattern: str) -> str:
    """Resolve the literal prefix of a pattern, leaving the glob part alone."""
    expanded = os.path.expanduser(pattern)
    cut = min(
        (expanded.index(c) for c in _GLOB_CHARS if c in expanded),
        default=len(expanded),
    )
    head, tail = expanded[:cut], expanded[cut:]
    if not head:
        return expanded
    # Resolve only whole directory components, so "/tmp/log*" resolves "/tmp/".
    boundary = max(head.rfind("/"), head.rfind(os.sep))
    if boundary == -1:
        return _resolve(head) + tail if not tail else _resolve(".") + os.sep + expanded
    literal, remainder = head[: boundary + 1], head[boundary + 1 :]
    return _resolve(literal).rstrip(os.sep) + os.sep + remainder + tail


def _glob(path: str, pattern: str) -> bool:
    import re

    regex = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            regex.append(r"(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            regex.append(r".*")
            index += 2
        elif char == "*":
            regex.append(r"[^/]*")
            index += 1
        elif char == "?":
            regex.append(r"[^/]")
            index += 1
        else:
            regex.append(re.escape(char))
            index += 1
    return re.fullmatch("".join(regex), path) is not None


# --- loading ------------------------------------------------------------------


def _strings(value: Any, where: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise PolicyError(f"{where} must be a string or a list of strings, not {value!r}")


def from_dict(data: dict[str, Any], source: Path | None = None) -> Policy:
    """Build a policy from already-parsed data."""
    if "default" not in data:
        # The module docstring promises the stance is a choice you have to make;
        # quietly picking "allow" here broke that promise in the one direction
        # that matters, since a guard policy that forgot the line would permit
        # every domain it did not mention.
        raise PolicyError(
            f"a policy must say default = {ALLOW!r} or default = {DENY!r}. "
            "There is no safe guess: 'allow' watches without stopping anything, "
            "'deny' refuses whatever the rules below do not name."
        )
    default = data["default"]
    if default not in (ALLOW, DENY):
        raise PolicyError(f"default must be {ALLOW!r} or {DENY!r}, not {default!r}")

    policy = Policy(default=default, source=source)
    for domain in ("filesystem", "process", "network", "database", "environment"):
        section = data.get(domain)
        if section is None:
            continue
        if not isinstance(section, dict):
            raise PolicyError(f"[{domain}] must be a table, not {type(section).__name__}")
        rules = {}
        for key, value in section.items():
            # Only where it is documented. Accepting it under [filesystem] meant
            # a policy could switch off database schema changes from a section
            # that has nothing to do with databases, and the key silently never
            # became a rule.
            if key == "allow_schema_changes" and domain == "database":
                if not isinstance(value, bool):
                    raise PolicyError("database.allow_schema_changes must be true or false")
                policy.allow_schema_changes = value
                continue
            if key == "allow_schema_changes":
                raise PolicyError(f"allow_schema_changes belongs under [database], not [{domain}]")
            rules[key] = _strings(value, f"{domain}.{key}")
        setattr(policy, domain, rules)
    return policy


def load(path: str | Path) -> Policy:
    """Read a policy file."""
    file = Path(path)
    try:
        with file.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise PolicyError(f"{file}: not valid TOML: {error}") from error
    except OSError as error:
        raise PolicyError(f"{file}: could not be read: {error}") from error
    return from_dict(data, source=file)


def permissive() -> Policy:
    """Everything allowed. For audit runs, where the point is to watch, not to stop."""
    return Policy(default=ALLOW)


def read_only() -> Policy:
    """Reads anywhere, and nothing else. A useful default for untrusted code."""
    return Policy(
        default=DENY,
        filesystem={"read": ["**"]},
    )
