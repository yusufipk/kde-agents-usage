"""Locate the state OpenCode keeps on disk.

OpenCode 1.2 and newer put every session in one SQLite file next to the
credentials it logs in with, and both helper scripts read from there:
fetch_usage.py for the Go plan key, token_stats.py for the per-message token
counters. The paths follow the same environment overrides the OpenCode binary
itself honours, so a relocated data directory is picked up as well.
"""

import os
import sqlite3

HOME = os.path.expanduser("~")
# A running OpenCode holds the database open in WAL mode; readers wait rather
# than fail when it is momentarily busy.
TIMEOUT = 5.0


def data_root():
    return os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")


def data_dir():
    """Directory holding opencode.db and auth.json."""
    # OpenCode resolves a relative OPENCODE_DB against the data root, an
    # absolute one against nothing, and treats it as the database itself.
    override = os.environ.get("OPENCODE_DB")
    if override and os.path.isabs(override):
        return os.path.dirname(override)
    return os.path.join(data_root(), "opencode")


def db_path():
    """Path of the session database, or None when there is none to read."""
    override = os.environ.get("OPENCODE_DB")
    if override == ":memory:":
        return None
    if override:
        return override if os.path.isabs(override) else os.path.join(data_root(), override)
    return os.path.join(data_dir(), "opencode.db")


def auth_path():
    return os.path.join(data_dir(), "auth.json")


def connect():
    """Open the session database read-only. Raises sqlite3.Error when unusable."""
    path = db_path()
    if not path:
        raise sqlite3.OperationalError("OpenCode keeps no database on disk")
    # Read-only so a running OpenCode is never blocked and its write-ahead log
    # is not checkpointed from under it.
    con = sqlite3.connect("file:%s?mode=ro" % path.replace("?", "%3f"), uri=True, timeout=TIMEOUT)
    con.execute("PRAGMA busy_timeout=%d" % int(TIMEOUT * 1000))
    return con
