"""Where the program meets the world, and where consequence stands in between.

Patching is done at the lowest layer that still knows what is going on. Patching
``os.unlink`` catches ``Path.unlink`` and ``shutil.rmtree`` for free, because
they call it. Patching ``Path.unlink`` as well would count the same deletion
twice, so it is left alone. The rule throughout is: intercept where the operation
becomes real, not where it was written.

The two exceptions are ``subprocess`` and ``sqlite3``, where the interesting
detail (the argv, the SQL) only exists at the top layer and is gone by the time
anything reaches a syscall.

Everything installed here is restored on exit, including when the body raises.
A library that leaves the standard library monkeypatched after an error is worse
than no library at all.
"""

from __future__ import annotations

import builtins
import contextlib
import errno
import io
import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from typing import Any

from consequence import effects as fx
from consequence.effects import Severity
from consequence.session import Network, active

#: Guards against consequence's own I/O being intercepted, which would recurse.
_busy = threading.local()

#: (module, attribute, original) for everything currently patched.
_installed: list[tuple[Any, str, Any]] = []
_install_lock = threading.Lock()

#: How many sessions are relying on the patches. Sessions nest — a plan inside an
#: audit, a fixture inside a session — and the inner one leaving must not strip
#: the interceptors out from under the outer one, which would leave it recording
#: nothing while it believes it is watching.
_depth = 0


def _reentrant() -> bool:
    return getattr(_busy, "flag", False)


class _Internal:
    """Marks a block as consequence's own work, so it is not recorded.

    Restores the previous value rather than clearing the flag, so that an inner
    block leaving does not un-mark the outer one. Clearing it would put the rest
    of the outer block back under interception, which for anything that reads a
    file means recursing through the patched ``open``.
    """

    def __enter__(self) -> None:
        self._previous = getattr(_busy, "flag", False)
        _busy.flag = True

    def __exit__(self, *exc: object) -> None:
        _busy.flag = self._previous


class InterceptionFailed(RuntimeError):
    """An interceptor could not be installed.

    Loud on purpose. A safety tool that quietly fails to hook something reports a
    clean plan for a program that deleted your database, which is worse than not
    running at all. The first version of this file swallowed exactly that.
    """


def _patch(module: Any, name: str, replacement: Callable[..., Any]) -> None:
    original = getattr(module, name)
    replacement.__consequence_original__ = original  # type: ignore[attr-defined]
    try:
        setattr(module, name, replacement)
    except (AttributeError, TypeError) as error:
        _tear_down()
        raise InterceptionFailed(
            f"could not intercept {getattr(module, '__name__', module)}.{name}: {error}. "
            "Refusing to continue, because a missing interceptor means a plan that "
            "understates what the code does."
        ) from error
    _installed.append((module, name, original))


def original_of(func: Any) -> Any:
    return getattr(func, "__consequence_original__", func)


# --- files --------------------------------------------------------------------


def _open_detail(mode: str, kind: str) -> str:
    """What the open is for, in words rather than in mode flags."""
    if "x" in mode:
        return "create, fail if it exists"
    if "a" in mode:
        return "append"
    if "w" in mode:
        return "overwrite" if "b" not in mode else "overwrite (binary)"
    if "+" in mode:
        return "read and write"
    return ""


def _open_kind(mode: str) -> tuple[str, Severity]:
    if "x" in mode:
        return fx.FILE_WRITE, Severity.CREATE
    if "a" in mode:
        return fx.FILE_APPEND, Severity.MODIFY
    if "w" in mode:
        return fx.FILE_WRITE, Severity.MODIFY
    if "+" in mode:
        return fx.FILE_WRITE, Severity.MODIFY
    return fx.FILE_READ, Severity.READ


def _decode_text(data: bytes, encoding: str | None) -> str:
    """Bytes as text mode would hand them over, line endings included.

    Text mode with the default ``newline=None`` turns ``\r\n`` and a lone
    ``\r`` into ``\n`` on the way in. Skipping that step makes a plan on
    Windows show the program the line endings the disk happens to have rather
    than the ones it would actually read, and an append then comes back with a
    stray ``\r`` in the middle of it.
    """
    text = data.decode(encoding or "utf-8", "replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _encode_text(text: str, encoding: str | None) -> bytes:
    """Text as text mode would put it on disk, ``os.linesep`` and all."""
    if os.linesep != "\n":
        text = text.replace("\n", os.linesep)
    return text.encode(encoding or "utf-8", "replace")


class _PlannedFile(io.StringIO):
    """A file that is not a file. On close, its contents go to the overlay."""

    def __init__(
        self,
        session: Any,
        path: str,
        append: bool,
        initial: str = "",
        encoding: str | None = None,
    ) -> None:
        super().__init__(initial)
        if append and initial:
            self.seek(0, io.SEEK_END)
        self._session = session
        self._path = path
        self._append = append
        self._encoding = encoding

    def close(self) -> None:
        if not self.closed:
            self._session.overlay.write(self._path, _encode_text(self.getvalue(), self._encoding))
        super().close()


class _PlannedBinaryFile(io.BytesIO):
    def __init__(self, session: Any, path: str, append: bool, initial: bytes = b"") -> None:
        super().__init__(initial)
        if append and initial:
            self.seek(0, io.SEEK_END)
        self._session = session
        self._path = path

    def close(self) -> None:
        if not self.closed:
            self._session.overlay.write(self._path, self.getvalue())
        super().close()


def _make_open(real: Callable[..., Any]) -> Callable[..., Any]:
    def opener(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        session = active()
        if session is None or _reentrant() or not isinstance(file, (str, bytes, os.PathLike)):
            return real(file, mode, *args, **kwargs)

        path = os.fspath(file)
        if isinstance(path, bytes):
            path = path.decode("utf-8", "replace")
        kind, severity = _open_kind(mode)
        detail = _open_detail(mode, kind)
        if severity is Severity.MODIFY and kind is fx.FILE_WRITE:
            # "overwrite" is a promise that something is being lost. Mode alone
            # cannot tell: open(path, "w") creates a new file just as often as it
            # replaces one, and calling both an overwrite makes the destructive
            # case impossible to pick out of the report.
            with _Internal():
                exists = session.overlay.exists(path)
            if not exists:
                severity, detail = Severity.CREATE, "create"

        with _Internal():
            effect = session.check(kind, path, detail, severity=severity)
        if not session.planning:
            session.enforce(effect)
            return real(file, mode, *args, **kwargs)

        # Plan mode.
        if kind is fx.FILE_READ:
            with _Internal():
                data = session.overlay.read(path)
            if data is None:
                return real(file, mode, *args, **kwargs)
            if "b" in mode:
                return io.BytesIO(data)
            return io.StringIO(_decode_text(data, kwargs.get("encoding")))

        session.enforce(effect)
        append = "a" in mode
        with _Internal():
            existing = session.overlay.read(path) if append else None
        if "b" in mode:
            return _PlannedBinaryFile(session, path, append, existing or b"")
        encoding = kwargs.get("encoding")
        return _PlannedFile(
            session, path, append, _decode_text(existing or b"", encoding), encoding
        )

    return opener


def _simple(kind: str, arg: int = 0, detail: str = "", planned: Any = None) -> Callable[..., Any]:
    """Wrap a function whose target is one of its positional arguments.

    ``planned`` is what the wrapper hands back in plan mode. None suits the
    functions that return nothing anyway; ``os.system`` returns an exit status,
    and None there reads as failure to every caller that checks it.
    """

    def wrap(real: Callable[..., Any]) -> Callable[..., Any]:
        def replacement(*args: Any, **kwargs: Any) -> Any:
            session = active()
            if session is None or _reentrant() or len(args) <= arg:
                return real(*args, **kwargs)
            target = args[arg]
            text = os.fspath(target) if isinstance(target, os.PathLike) else str(target)
            with _Internal():
                effect = session.check(kind, text, detail)
            session.enforce(effect)
            if session.planning:
                with _Internal():
                    _simulate(session, kind, text, args)
                return planned
            return real(*args, **kwargs)

        return replacement

    return wrap


def _already_a_directory(session: Any, target: str) -> bool:
    with _Internal():
        return session.overlay.exists(target) and os.path.isdir(target)


def _make_mkdir(real: Callable[..., Any], name: str) -> Callable[..., Any]:
    """Directory creation, which needs to fail the way the real thing fails.

    ``os.mkdir`` raises FileExistsError on a directory that is already there,
    and callers depend on it: both ``Path.mkdir(exist_ok=True)`` and
    ``os.makedirs(exist_ok=True)`` are implemented by catching that exception.
    A plan that quietly returns None instead swallows the signal, so the caller
    believes it created something and the report grows a line for a directory
    that has existed for months.

    Found by planning a real migration tool, whose plan claimed it would create
    the directory the database was already sitting in.
    """

    def replacement(path: Any, *rest: Any, **kwargs: Any) -> Any:
        session = active()
        if session is None or _reentrant():
            return real(path, *rest, **kwargs)
        text = os.fspath(path) if isinstance(path, os.PathLike) else str(path)

        if _already_a_directory(session, text):
            if name == "makedirs" and kwargs.get("exist_ok", False):
                # Genuinely a no-op. Not delegating to the real makedirs,
                # because it reaches its decision by calling the patched
                # os.mkdir, and that inner call is what would be recorded.
                return None
            if session.planning:
                raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), text)
            return real(path, *rest, **kwargs)

        with _Internal():
            effect = session.check(fx.DIR_CREATE, text)
        session.enforce(effect)
        if session.planning:
            with _Internal():
                session.overlay.mkdir(text)
            return None
        return real(path, *rest, **kwargs)

    return replacement


def _simulate(session: Any, kind: str, target: str, args: tuple[Any, ...]) -> None:
    overlay = session.overlay
    if kind in (fx.FILE_DELETE,):
        overlay.delete(target)
    elif kind is fx.DIR_DELETE:
        overlay.delete_tree(target)
    elif kind is fx.DIR_CREATE:
        overlay.mkdir(target)
    elif kind in (fx.FILE_MOVE, fx.FILE_COPY) and len(args) > 1:
        destination = args[1]
        text = os.fspath(destination) if isinstance(destination, os.PathLike) else str(destination)
        if kind is fx.FILE_MOVE:
            overlay.move(target, text)
        else:
            data = overlay.read(target)
            if data is not None:
                overlay.write(text, data)


def _two_path(kind: str) -> Callable[..., Any]:
    """For move and copy, where the detail is the destination."""

    def wrap(real: Callable[..., Any]) -> Callable[..., Any]:
        def replacement(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
            session = active()
            if session is None or _reentrant():
                return real(src, dst, *args, **kwargs)
            source = os.fspath(src) if isinstance(src, os.PathLike) else str(src)
            destination = os.fspath(dst) if isinstance(dst, os.PathLike) else str(dst)
            with _Internal():
                effect = session.check(kind, source, f"to {destination}")
            session.enforce(effect)
            if session.planning:
                with _Internal():
                    _simulate(session, kind, source, (src, dst))
                return destination
            return real(src, dst, *args, **kwargs)

        return replacement

    return wrap


# --- processes ----------------------------------------------------------------


def _argv_text(args: Any) -> str:
    if isinstance(args, (list, tuple)):
        return " ".join(str(a) for a in args)
    return str(args)


class _PlannedProcess:
    """Stands in for a process that was never started."""

    def __init__(self, argv: str) -> None:
        self.args = argv
        self.returncode = 0
        self.pid = -1
        self.stdout = None
        self.stderr = None
        self.stdin = None

    def communicate(self, *_a: Any, **_k: Any) -> tuple[Any, Any]:
        return (b"", b"")

    def wait(self, *_a: Any, **_k: Any) -> int:
        return 0

    def poll(self) -> int:
        return 0

    def kill(self) -> None:
        return None

    terminate = kill

    def __enter__(self) -> _PlannedProcess:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _planned_result(name: str, args: Any, kwargs: dict[str, Any]) -> Any:
    """What the real function would have returned, in the shape it returns it.

    One stand-in for all four of run, call, check_call and check_output is not
    good enough: ``subprocess.call(cmd) != 0`` on a CompletedProcess is always
    true, so a planned program takes the failure branch of every shell-out it
    does and the plan describes a run that never happens. Each name gets its own
    type back.
    """
    if name == "check_output":
        text = bool(
            kwargs.get("text") or kwargs.get("encoding") or kwargs.get("universal_newlines")
        )
        return "" if text else b""
    if name in ("call", "check_call"):
        return 0
    return subprocess.CompletedProcess(args=args, returncode=0, stdout=b"", stderr=b"")


def _make_run(real: Callable[..., Any], name: str) -> Callable[..., Any]:
    def replacement(args: Any, *rest: Any, **kwargs: Any) -> Any:
        session = active()
        if session is None or _reentrant():
            return real(args, *rest, **kwargs)
        argv = _argv_text(args)
        with _Internal():
            effect = session.check(fx.PROCESS_SPAWN, argv.split(" ")[0], argv)
        session.enforce(effect)
        if session.planning:
            return _planned_result(name, args, kwargs)
        return real(args, *rest, **kwargs)

    return replacement


def _make_popen(real: Any) -> Any:
    def replacement(args: Any, *rest: Any, **kwargs: Any) -> Any:
        session = active()
        if session is None or _reentrant():
            return real(args, *rest, **kwargs)
        argv = _argv_text(args)
        with _Internal():
            effect = session.check(fx.PROCESS_SPAWN, argv.split(" ")[0], argv)
        session.enforce(effect)
        if session.planning:
            return _PlannedProcess(argv)
        return real(args, *rest, **kwargs)

    return replacement


# --- network ------------------------------------------------------------------


def _make_connect(real: Callable[..., Any]) -> Callable[..., Any]:
    def replacement(self: Any, address: Any, *args: Any, **kwargs: Any) -> Any:
        session = active()
        if session is None or _reentrant():
            return real(self, address, *args, **kwargs)
        host, port = _address(address)
        with _Internal():
            effect = session.check(fx.NET_CONNECT, host, f"port {port}" if port else "")
        session.enforce(effect)
        if session.planning and session.network is Network.BLOCK:
            session.block(
                effect,
                "There is no honest way to invent a reply. Pass network='allow' to let "
                "real requests through during a plan, or stub the client yourself.",
            )
        return real(self, address, *args, **kwargs)

    return replacement


def _address(address: Any) -> tuple[str, Any]:
    if isinstance(address, tuple) and len(address) >= 2:
        return str(address[0]), address[1]
    return str(address), None


def _make_request(real: Callable[..., Any]) -> Callable[..., Any]:
    def replacement(self: Any, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        session = active()
        if session is None or _reentrant():
            return real(self, method, url, *args, **kwargs)
        host = getattr(self, "host", "?")
        with _Internal():
            effect = session.check(fx.NET_REQUEST, host, f"{method} {url}")
        session.enforce(effect)
        return real(self, method, url, *args, **kwargs)

    return replacement


# --- databases ----------------------------------------------------------------

_SCHEMA_WORDS = ("drop", "truncate", "alter")
_MUTATE_WORDS = ("insert", "update", "delete", "replace", "create", "merge", "upsert")


def classify_sql(sql: str) -> tuple[str, Severity]:
    """What a statement would do, from its first keyword.

    Deliberately coarse. The distinction that matters is between a statement that
    reads and one that does not, and a first-word check gets that right without
    pretending to parse SQL.
    """
    first = sql.strip().lstrip("(").split(None, 1)
    word = first[0].lower() if first else ""
    if word in _SCHEMA_WORDS:
        return fx.DB_SCHEMA, Severity.DESTROY
    if word in _MUTATE_WORDS:
        severity = Severity.CREATE if word in ("insert", "create") else Severity.MODIFY
        return fx.DB_MUTATE, severity
    return fx.DB_QUERY, Severity.READ


def _make_execute(real: Callable[..., Any]) -> Callable[..., Any]:
    def replacement(self: Any, sql: str = "", *args: Any, **kwargs: Any) -> Any:
        session = active()
        if session is None or _reentrant() or not isinstance(sql, str):
            return real(self, sql, *args, **kwargs)
        kind, severity = classify_sql(sql)
        target = _database_name(self)
        with _Internal():
            effect = session.check(kind, target, " ".join(sql.split())[:160], severity=severity)
        session.enforce(effect)
        if session.planning and kind is not fx.DB_QUERY:
            return _PlannedCursor()
        return real(self, sql, *args, **kwargs)

    return replacement


class _PlannedCursor:
    """A cursor for a statement that was never run."""

    rowcount = 0
    lastrowid = None

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[Any]:
        return []

    def fetchmany(self, *_a: Any) -> list[Any]:
        return []

    def __iter__(self) -> Any:
        return iter(())


def _database_name(cursor_or_connection: Any) -> str:
    for attribute in ("_consequence_name", "database"):
        value = getattr(cursor_or_connection, attribute, None)
        if isinstance(value, str):
            return value
    connection = getattr(cursor_or_connection, "connection", None)
    if connection is not None and connection is not cursor_or_connection:
        return _database_name(connection)
    return "database"


# --- environment --------------------------------------------------------------


def _make_setenv(real: Callable[..., Any]) -> Callable[..., Any]:
    def replacement(self: Any, key: Any, value: Any) -> Any:
        session = active()
        if session is None or _reentrant():
            return real(self, key, value)
        with _Internal():
            effect = session.check(fx.ENV_CHANGE, str(key), "set")
        session.enforce(effect)
        if session.planning:
            return None
        return real(self, key, value)

    return replacement


# --- install / uninstall ------------------------------------------------------


def install() -> None:
    """Put every interceptor in place. Nested sessions share one set."""
    global _depth
    with _install_lock:
        _depth += 1
        if _installed:
            return

        _patch(builtins, "open", _make_open(builtins.open))
        _patch(io, "open", _make_open(io.open))

        for name, kind in (
            ("remove", fx.FILE_DELETE),
            ("unlink", fx.FILE_DELETE),
            ("rmdir", fx.DIR_DELETE),
            ("removedirs", fx.DIR_DELETE),
            ("truncate", fx.FILE_WRITE),
            ("chmod", fx.PERM_CHANGE),
        ):
            if hasattr(os, name):
                _patch(os, name, _simple(kind)(getattr(os, name)))

        for name in ("mkdir", "makedirs"):
            _patch(os, name, _make_mkdir(getattr(os, name), name))

        for name in ("rename", "replace", "link", "symlink"):
            if hasattr(os, name):
                _patch(os, name, _two_path(fx.FILE_MOVE)(getattr(os, name)))

        _patch(shutil, "rmtree", _simple(fx.DIR_DELETE)(shutil.rmtree))
        for name in ("copy", "copy2", "copyfile", "copytree"):
            if hasattr(shutil, name):
                _patch(shutil, name, _two_path(fx.FILE_COPY)(getattr(shutil, name)))
        _patch(shutil, "move", _two_path(fx.FILE_MOVE)(shutil.move))

        _patch(os, "system", _simple(fx.PROCESS_SPAWN, planned=0)(os.system))
        if hasattr(os, "kill"):
            _patch(os, "kill", _simple(fx.PROCESS_SIGNAL)(os.kill))
        for name in ("run", "call", "check_call", "check_output"):
            if hasattr(subprocess, name):
                _patch(subprocess, name, _make_run(getattr(subprocess, name), name))
        _patch(subprocess, "Popen", _make_popen(subprocess.Popen))

        import socket

        _patch(socket.socket, "connect", _make_connect(socket.socket.connect))
        _patch(socket.socket, "connect_ex", _make_connect(socket.socket.connect_ex))
        with contextlib.suppress(Exception):  # pragma: no branch
            import http.client

            _patch(
                http.client.HTTPConnection,
                "request",
                _make_request(http.client.HTTPConnection.request),
            )

        import sqlite3

        from consequence.databases import make_connect

        # Connection and Cursor are C types and will not take a patched method.
        # The authorizer installed by this wrapper is what actually intercepts
        # statements, and it does so inside the engine.
        _patch(sqlite3, "connect", make_connect(sqlite3.connect))

        _patch(type(os.environ), "__setitem__", _make_setenv(type(os.environ).__setitem__))

        _repoint_pathlib_accessor()


#: Where 3.10's pathlib cached each function it uses.
_ACCESSOR_SOURCES: dict[str, Any] = {
    "open": io,
    "unlink": os,
    "rmdir": os,
    "mkdir": os,
    "rename": os,
    "replace": os,
    "link": os,
    "symlink": os,
    "chmod": os,
}


def _repoint_pathlib_accessor() -> None:
    """Point 3.10's pathlib at the patched functions instead of its own copies.

    On 3.10, ``pathlib`` reads ``io.open`` and the ``os`` functions once at
    class-definition time and keeps them on a private accessor object. Patching
    the modules afterwards therefore does nothing at all, and
    ``Path.write_text`` writes straight to disk during a plan. CI on 3.10 is
    what found that; every other version calls ``os`` and ``io`` directly and
    has no accessor to fix.

    The replacements go on the singleton instance rather than on the class. They
    are ordinary Python functions, so as class attributes they would bind on
    access and arrive with the accessor as their first argument, which the
    cached builtins never did.
    """
    import pathlib

    accessor = getattr(pathlib, "_normal_accessor", None)
    if accessor is None:
        return
    for name, module in _ACCESSOR_SOURCES.items():
        patched = getattr(module, name, None)
        cached = getattr(accessor, name, None)
        # Only re-point what is genuinely intercepted, and only where pathlib
        # really is holding its own copy.
        if patched is None or cached is None:
            continue
        if not hasattr(patched, "__consequence_original__"):
            continue
        if cached is not original_of(patched):
            continue
        setattr(accessor, name, patched)
        _installed.append((accessor, name, cached))


def _tear_down() -> None:
    """Unconditionally remove every patch, whatever the depth.

    Used when installation itself fails part-way. Counting down politely from a
    half-installed state would leave the process patched with an interceptor set
    nobody can name.
    """
    global _depth
    _depth = 0
    while _installed:
        module, name, original = _installed.pop()
        with contextlib.suppress(Exception):
            setattr(module, name, original)


def uninstall() -> None:
    """Release one session's hold, and put everything back once none are left."""
    global _depth
    with _install_lock:
        _depth = max(0, _depth - 1)
        if _depth:
            return
        while _installed:
            module, name, original = _installed.pop()
            with contextlib.suppress(Exception):
                setattr(module, name, original)


def installed() -> bool:
    return bool(_installed)
