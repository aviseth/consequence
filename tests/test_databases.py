"""SQLite, intercepted through the authorizer rather than by reading SQL."""

import sqlite3

import pytest

import consequence
from consequence import Denied
from consequence.effects import DB_MUTATE, DB_SCHEMA
from consequence.policy import from_dict


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "app.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE sessions (id INTEGER)")
    connection.execute("CREATE TABLE audit (id INTEGER)")
    connection.execute("INSERT INTO audit VALUES (1)")
    connection.commit()
    connection.close()
    return path


def tables(path):
    connection = sqlite3.connect(path)
    try:
        return {
            r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()


def rows(path, table):
    connection = sqlite3.connect(path)
    try:
        return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


def test_a_drop_is_reported_as_a_drop_not_as_a_delete(database):
    """Dropping a table makes SQLite ask about sqlite_master first, and reporting
    that instead would describe the most destructive statement as a milder one."""
    with consequence.plan() as run:
        connection = sqlite3.connect(database)
        connection.execute("DROP TABLE sessions")
    schema = [e for e in run.effects if e.kind == DB_SCHEMA]
    assert len(schema) == 1
    assert "drop table sessions" in schema[0].detail
    assert schema[0].destructive


def test_plan_mode_does_not_drop(database):
    with consequence.plan():
        connection = sqlite3.connect(database)
        connection.execute("DROP TABLE sessions")
        connection.commit()
    assert "sessions" in tables(database)


def test_plan_mode_does_not_delete_rows(database):
    with consequence.plan() as run:
        connection = sqlite3.connect(database)
        connection.execute("DELETE FROM audit")
        connection.commit()
    assert rows(database, "audit") == 1
    assert any(e.kind == DB_MUTATE for e in run.effects)


def test_plan_mode_does_not_insert(database):
    with consequence.plan():
        connection = sqlite3.connect(database)
        connection.execute("INSERT INTO audit VALUES (2)")
        connection.commit()
    assert rows(database, "audit") == 1


def test_reads_still_work_in_plan_mode(database):
    """A program that cannot read cannot get far enough to be worth planning."""
    with consequence.plan():
        connection = sqlite3.connect(database)
        assert connection.execute("SELECT count(*) FROM audit").fetchone()[0] == 1


def test_executescript_is_intercepted_too(database):
    """A wrapper round Connection.execute would miss this entirely."""
    with consequence.plan() as run:
        connection = sqlite3.connect(database)
        connection.executescript("DELETE FROM audit; DROP TABLE sessions;")
    assert rows(database, "audit") == 1
    assert "sessions" in tables(database)
    assert any(e.kind == DB_SCHEMA for e in run.effects)


def test_schema_changes_can_be_refused_by_policy(database):
    policy = from_dict({"database": {"allow": ["*"], "allow_schema_changes": False}})
    with consequence.guard(policy):
        connection = sqlite3.connect(database)
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("DROP TABLE sessions")
    assert "sessions" in tables(database)


def test_guard_lets_allowed_statements_through(database):
    policy = from_dict({"default": "allow"})
    with consequence.guard(policy):
        connection = sqlite3.connect(database)
        connection.execute("INSERT INTO audit VALUES (2)")
        connection.commit()
    assert rows(database, "audit") == 2


def test_opening_a_connection_is_itself_recorded(database):
    with consequence.audit() as run:
        sqlite3.connect(database).close()
    assert any("open connection" in e.detail for e in run.effects)


def test_a_denied_connection_stops_before_opening(tmp_path):
    policy = from_dict({"default": "deny"})
    with pytest.raises(Denied), consequence.guard(policy):
        sqlite3.connect(tmp_path / "new.db")


def test_sqlites_own_catalogue_is_not_reported(database):
    """Internal bookkeeping would bury the report in noise nobody can act on."""
    with consequence.plan() as run:
        connection = sqlite3.connect(database)
        connection.execute("DROP TABLE sessions")
    assert not any("sqlite_" in e.detail for e in run.effects)


def test_a_drop_is_reported_once_not_as_a_drop_plus_a_delete(database):
    """SQLite asks about the table's rows as well, which would pad the plan."""
    with consequence.plan() as run:
        connection = sqlite3.connect(database)
        connection.execute("DROP TABLE sessions")
    about_sessions = [e for e in run.effects if "sessions" in e.detail]
    assert len(about_sessions) == 1
    assert about_sessions[0].kind == DB_SCHEMA


def test_a_real_delete_is_still_reported(database):
    """The de-duplication must not swallow a delete that stands on its own."""
    with consequence.plan() as run:
        connection = sqlite3.connect(database)
        connection.execute("DELETE FROM sessions")
    assert [e.kind for e in run.effects if e.kind == DB_MUTATE]
