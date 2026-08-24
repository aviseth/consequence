"""Build the little project the other examples operate on.

Everything lands in ./demo, which is disposable. Run this first.
"""

import shutil
import sqlite3
from pathlib import Path

HERE = Path(__file__).parent
DEMO = HERE / "demo"


def build() -> Path:
    if DEMO.exists():
        shutil.rmtree(DEMO)
    (DEMO / "config").mkdir(parents=True)
    (DEMO / "config" / "app.toml").write_text("[app]\ndebug = true\nworkers = 2\n")
    (DEMO / "keep-me.log").write_text("something you would rather keep\n")

    connection = sqlite3.connect(DEMO / "app.db")
    connection.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, token TEXT)")
    connection.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, created_at TEXT)")
    connection.execute("INSERT INTO sessions VALUES (1, 'abc')")
    connection.execute("INSERT INTO audit_log VALUES (1, '2020-01-01')")
    connection.commit()
    connection.close()

    shutil.copy(HERE / "cleanup_agent.py", DEMO / "cleanup_agent.py")
    shutil.copy(HERE / "safe.toml", DEMO / "safe.toml")
    return DEMO


if __name__ == "__main__":
    print(f"built {build()}")
