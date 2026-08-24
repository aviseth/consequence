"""SQLite, intercepted at the engine rather than at the string.

The obvious approach is to wrap ``Connection.execute`` and read the SQL. It does
not work: ``sqlite3.Connection`` is a C type and will not accept a patched
attribute, which the first version of this file discovered the hard way by
silently letting a ``DROP TABLE`` through while reporting a clean plan.

So this uses the authorizer callback instead. SQLite calls it while preparing
every statement, with a structured action code and the table and column names
already parsed, and the return value decides whether the statement is allowed to
proceed. That is better than string matching in three ways: it cannot be fooled
by unusual SQL, it sees statements executed through any API including
``executescript`` and triggers, and denial happens inside the engine rather than
in a wrapper somebody could bypass.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from consequence import effects as fx
from consequence.effects import Severity
from consequence.errors import ConsequenceError


class TooLargeToPlan(ConsequenceError):
    """A database too big to copy into memory for a plan.

    A configured limit rather than an allocation failure, so not a MemoryError:
    the CLI turns package errors into a message and exit code 2, and a
    MemoryError would come out as a traceback.
    """


#: Authorizer action code -> (effect kind, severity, verb). SQLite passes many
#: more; the ones absent from this table are reads and bookkeeping.
ACTIONS: dict[int, tuple[str, Severity, str]] = {
    sqlite3.SQLITE_DELETE: (fx.DB_MUTATE, Severity.MODIFY, "delete from"),
    sqlite3.SQLITE_INSERT: (fx.DB_MUTATE, Severity.CREATE, "insert into"),
    sqlite3.SQLITE_UPDATE: (fx.DB_MUTATE, Severity.MODIFY, "update"),
    sqlite3.SQLITE_DROP_TABLE: (fx.DB_SCHEMA, Severity.DESTROY, "drop table"),
    sqlite3.SQLITE_DROP_TEMP_TABLE: (fx.DB_SCHEMA, Severity.DESTROY, "drop temp table"),
    sqlite3.SQLITE_DROP_INDEX: (fx.DB_SCHEMA, Severity.DESTROY, "drop index"),
    sqlite3.SQLITE_DROP_VIEW: (fx.DB_SCHEMA, Severity.DESTROY, "drop view"),
    sqlite3.SQLITE_DROP_TRIGGER: (fx.DB_SCHEMA, Severity.DESTROY, "drop trigger"),
    sqlite3.SQLITE_CREATE_TABLE: (fx.DB_SCHEMA, Severity.CREATE, "create table"),
    sqlite3.SQLITE_CREATE_INDEX: (fx.DB_SCHEMA, Severity.CREATE, "create index"),
    sqlite3.SQLITE_CREATE_VIEW: (fx.DB_SCHEMA, Severity.CREATE, "create view"),
    sqlite3.SQLITE_CREATE_TRIGGER: (fx.DB_SCHEMA, Severity.CREATE, "create trigger"),
    sqlite3.SQLITE_ALTER_TABLE: (fx.DB_SCHEMA, Severity.DESTROY, "alter table"),
    sqlite3.SQLITE_ATTACH: (fx.DB_SCHEMA, Severity.MODIFY, "attach"),
    sqlite3.SQLITE_DETACH: (fx.DB_SCHEMA, Severity.MODIFY, "detach"),
}

#: Reads, recorded but never blocked by severity alone.
READS = {sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT}

#: SQLite's own catalogue. Dropping a table makes the engine ask permission to
#: delete from sqlite_master first, and reporting that instead of the drop would
#: describe a DROP TABLE as a DELETE, which is both wrong and less alarming than
#: the truth. Answering OK here lets the real action code arrive next.
INTERNAL_PREFIX = "sqlite_"


def authorizer_for(connection_name: str) -> Any:
    """Build an authorizer bound to one database, for the active session."""

    # Tables dropped by this connection. SQLite reports DROP TABLE as the drop
    # and then, separately, as a delete of the table's rows, so the same
    # statement arrives twice. The drop is the honest description; the delete
    # that follows it is withdrawn. Kept per connection because the pairing is a
    # property of one statement on one connection, not of the session.
    just_dropped: set[str] = set()

    def authorize(action: int, arg1: Any, arg2: Any, dbname: Any, trigger: Any) -> int:
        from consequence.intercept import _Internal, _reentrant
        from consequence.session import active

        session = active()
        if session is None or _reentrant():
            return sqlite3.SQLITE_OK

        if action in READS:
            # Recording every column read would bury the report in noise, and a
            # read is not what anyone is worried about.
            return sqlite3.SQLITE_OK

        entry = ACTIONS.get(action)
        if entry is None:
            return sqlite3.SQLITE_OK
        kind, severity, verb = entry
        table = str(arg1) if arg1 else ""
        if table.startswith(INTERNAL_PREFIX):
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_DELETE and table in just_dropped:
            just_dropped.discard(table)
            return sqlite3.SQLITE_OK
        if kind is fx.DB_SCHEMA and verb.startswith("drop") and table:
            just_dropped.add(table)

        with _Internal():
            effect = session.check(
                kind,
                connection_name,
                f"{verb} {table}".strip(),
                severity=severity,
                table=table,
                action=action,
            )
        if not effect.allowed:
            # SQLITE_DENY makes the engine raise, which surfaces at the call site
            # as a DatabaseError. The session already holds the reason.
            return sqlite3.SQLITE_DENY
        # In plan mode the statement is allowed to run, because it is running
        # against a private in-memory copy and cannot reach the real database.
        # SQLITE_IGNORE would be the obvious thing to return here and it does not
        # work: it is documented for reads and for triggers, and a top-level
        # DELETE proceeds regardless. That was found by a test that checked the
        # row count afterwards rather than trusting the return value.
        return sqlite3.SQLITE_OK

    return authorize


#: Above this, copying a database into memory for a plan is not a kindness.
MAX_PLAN_COPY_BYTES = 512 * 1024 * 1024


def make_connect(real: Any) -> Any:
    """Wrap ``sqlite3.connect``, and in plan mode hand back a private copy.

    Plan mode gets an in-memory database seeded from the real file. Statements
    then run for real against that copy, so the program reads back its own
    inserts and updates exactly as it would have, while the file on disk is never
    opened for writing. Same bargain as the filesystem overlay: a faithful world
    that is not the real one.

    Refusing the statements instead was tried first and is wrong twice over. The
    program stops at its first write, so the plan is truncated at the very point
    it becomes interesting. And it only stops if the engine cooperates, which for
    a top-level DELETE it does not: SQLITE_IGNORE is documented for reads and for
    triggers, and the rows went anyway. A test that counted rows afterwards
    caught that; one that trusted the return value would not have.
    """

    def connect(database: Any = ":memory:", *args: Any, **kwargs: Any) -> Any:
        import os

        from consequence.intercept import _Internal, _reentrant
        from consequence.session import active

        session = active()
        if session is None or _reentrant():
            return real(database, *args, **kwargs)

        name = str(database)
        with _Internal():
            effect = session.check(fx.DB_QUERY, name, "open connection", severity=Severity.READ)
        session.enforce(effect)

        if not session.planning:
            connection = real(database, *args, **kwargs)
            connection.set_authorizer(authorizer_for(name))
            return connection

        with _Internal():
            if name != ":memory:" and os.path.exists(name):
                size = os.path.getsize(name)
                if size > MAX_PLAN_COPY_BYTES:
                    raise TooLargeToPlan(
                        f"{name} is {size / 1e9:.1f} GB, too large to copy into memory for "
                        "a plan. Point the program at a smaller database, or use audit "
                        "mode against a restored snapshot."
                    )
            copy = real(":memory:")
            if name != ":memory:" and os.path.exists(name):
                source = real(name)
                try:
                    source.backup(copy)
                finally:
                    source.close()
        copy.set_authorizer(authorizer_for(name))
        return copy

    return connect
