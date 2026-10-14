"""SQLite store. One file, WAL mode, one connection per thread/process.

Every state change goes through `tx()`, which takes SQLite's write lock up front
(BEGIN IMMEDIATE). That lock is the single serialization point for the whole
payment layer: two threads or two processes can never interleave inside a
money-moving operation.
"""
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  id      TEXT PRIMARY KEY,
  balance INTEGER NOT NULL CHECK (balance >= 0)          -- backstop: never negative
);
CREATE TABLE IF NOT EXISTS mints(                          -- the ONLY way money enters
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT NOT NULL,
  amount  INTEGER NOT NULL CHECK (amount > 0)
);
CREATE TABLE IF NOT EXISTS transfers(                      -- append-only journal
  idem_key TEXT PRIMARY KEY,                               -- replay => same row
  src      TEXT NOT NULL,
  dst      TEXT NOT NULL,
  amount   INTEGER NOT NULL CHECK (amount > 0),
  status   TEXT NOT NULL CHECK (status IN ('ok','rejected')),
  reason   TEXT,
  ts       REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS escrows(
  task_id    TEXT PRIMARY KEY,
  buyer      TEXT NOT NULL,
  seller     TEXT,
  amount     INTEGER NOT NULL CHECK (amount > 0),
  spec       TEXT NOT NULL,
  state      TEXT NOT NULL CHECK (state IN
               ('OPEN','ASSIGNED','SUBMITTED','RELEASED','REFUNDED')),
  created_at REAL NOT NULL,
  deadline   REAL NOT NULL,
  output     TEXT,
  verdict    TEXT,
  settled_reason TEXT
);
CREATE TABLE IF NOT EXISTS agents(
  id         TEXT PRIMARY KEY,
  reputation INTEGER NOT NULL DEFAULT 0,
  capacity   INTEGER NOT NULL DEFAULT 1                    -- concurrent tasks a seller takes
);
CREATE TABLE IF NOT EXISTS caps(
  agent              TEXT PRIMARY KEY,
  max_spend_per_hour INTEGER,
  max_tasks_per_hour INTEGER
);
CREATE TABLE IF NOT EXISTS spend_log(
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  agent   TEXT NOT NULL,
  amount  INTEGER NOT NULL,
  is_task INTEGER NOT NULL,
  ts      REAL NOT NULL
);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        c = self.conn()
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None,
                                check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA busy_timeout=30000")
            self._local.c = c
        return c

    @contextmanager
    def tx(self):
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")
