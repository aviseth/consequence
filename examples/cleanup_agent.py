"""The kind of script you get when you ask something to "tidy up the project".

It is not malicious. It is plausible, and it is wrong in one specific way, and
you cannot see which way by reading it quickly. That is the whole problem.

Run it under `consequence plan` before you run it for real.
"""

import shutil
import sqlite3
import subprocess
from pathlib import Path

STALE_DAYS = 30


def main() -> None:
    root = Path.cwd()

    # Rewrite the config with the values this run decided on.
    config = root / "config" / "app.toml"
    config.write_text("[app]\ndebug = false\nworkers = 8\n")

    # Clear out what looks like build detritus.
    shutil.rmtree(root / "config")
    (root / "keep-me.log").unlink()

    # Trim the database.
    connection = sqlite3.connect(root / "app.db")
    connection.execute("DROP TABLE IF EXISTS sessions")
    connection.execute("DELETE FROM audit_log WHERE created_at < date('now', '-30 days')")
    connection.commit()

    # Publish the tidied tree.
    subprocess.run(["git", "push", "--force", "origin", "main"], check=False)


if __name__ == "__main__":
    main()
