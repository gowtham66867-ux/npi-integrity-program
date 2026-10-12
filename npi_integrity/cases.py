"""Durable case state, append-only events, drafts and shared draft budgets.

Local runs use SQLite; deployed runs require Firestore. The immutable detection
warehouse is separate from mutable reviewer state. Case identity is based on
NPI and evidence fingerprint, so container rebuilds do not reset dispositions.
"""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from fastapi import HTTPException
from .security import check_transition

STATUS = {"review": "In review", "monitor": "Monitoring", "dismiss": "Dismissed",
          "escalate": "Escalated", "refer": "Referred", "reopen": "In review"}


def case_key(alert):
    return hashlib.sha256(f"{alert['npi']}:{alert['fingerprint']}".encode()).hexdigest()


def event(actor, action, obj, details=None):
    return {"ts": datetime.now(timezone.utc).isoformat(), "actor": actor,
            "action": action, "object": obj, "details": details or {}}


class SQLiteCases:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS cases (key TEXT PRIMARY KEY, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, body TEXT);
                CREATE TABLE IF NOT EXISTS drafts (key TEXT PRIMARY KEY, body TEXT);
                CREATE TABLE IF NOT EXISTS budget (key TEXT PRIMARY KEY, times TEXT);
                CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
                  BEGIN SELECT RAISE(ABORT, 'audit events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
                  BEGIN SELECT RAISE(ABORT, 'audit events are immutable'); END;
            ''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def states(self, keys):
        if not keys:
            return {}
        with self.connect() as c:
            return dict(c.execute(f"SELECT key,status FROM cases WHERE key IN ({','.join('?' for _ in keys)})", keys))

    def transition(self, key, initial, alert_id, d):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT status FROM cases WHERE key=?", [key]).fetchone()
            check_transition(row[0] if row else initial, d.action)
            status = STATUS[d.action]
            c.execute("INSERT INTO cases VALUES (?,?) ON CONFLICT(key) DO UPDATE SET status=excluded.status", [key, status])
            e = event(d.actor, 'disposition:' + d.action, alert_id,
                      {"reason": d.reason, "supervisor": d.supervisor, "status": status, "case_key": key})
            c.execute("INSERT INTO events(key,body) VALUES (?,?)", [key, json.dumps(e)])
        return status

    def history(self, key):
        return [e for e in self._events(key=key) if e['action'].startswith('disposition:')]

    def _events(self, limit=1000, key=None):
        with self.connect() as c:
            records = c.execute("SELECT seq,body FROM events " + ("WHERE key=? " if key else "") +
                                "ORDER BY seq DESC LIMIT ?", ([key] if key else []) + [limit]).fetchall()
        return [{**json.loads(body), "seq": seq} for seq, body in records]

    def audit(self, limit):
        return self._events(limit)

    def record(self, key, e):
        with self.connect() as c:
            c.execute("INSERT INTO events(key,body) VALUES (?,?)", [key, json.dumps(e, default=str)])

    def save_draft(self, key, result, e):
        with self.connect() as c:
            c.execute("INSERT INTO drafts VALUES (?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body",
                      [key, json.dumps(result, default=str)])
            c.execute("INSERT INTO events(key,body) VALUES (?,?)", [key, json.dumps(e, default=str)])

    def latest_draft(self, key):
        with self.connect() as c:
            row = c.execute("SELECT body FROM drafts WHERE key=?", [key]).fetchone()
        return json.loads(row[0]) if row else None

    def reserve_draft(self, limit, now=None):
        now = time.time() if now is None else now
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT times FROM budget WHERE key='drafts'").fetchone()
            times = [t for t in json.loads(row[0] if row else '[]') if now-t < 60]
            if len(times) >= limit:
                raise HTTPException(429, "agent draft rate limit reached; try again in a minute")
            c.execute("INSERT INTO budget VALUES ('drafts',?) ON CONFLICT(key) DO UPDATE SET times=excluded.times",
                      [json.dumps(times + [now])])


class FirestoreCases:
    def __init__(self, project, database='npi-cases', namespace='npi_integrity'):
        from google.cloud import firestore
        self.fs = firestore
        self.client = firestore.Client(project=project, database=database)
        self.root = self.client.collection(namespace).document('v1')

    def states(self, keys):
        refs = [self.root.collection('cases').document(k) for k in keys]
        return {s.id: s.to_dict()['status'] for s in self.client.get_all(refs) if s.exists} if refs else {}

    def transition(self, key, initial, alert_id, d):
        ref = self.root.collection('cases').document(key)
        evref = self.root.collection('events').document(uuid.uuid4().hex)
        @self.fs.transactional
        def apply(tx):
            snap = ref.get(transaction=tx)
            check_transition(snap.to_dict()['status'] if snap.exists else initial, d.action)
            status = STATUS[d.action]
            tx.set(ref, {"status": status})
            tx.create(evref, {**event(d.actor, 'disposition:' + d.action, alert_id,
                                    {"reason": d.reason, "supervisor": d.supervisor, "status": status, "case_key": key}),
                              "key": key})
            return status
        return apply(self.client.transaction())

    def history(self, key):
        # Single equality query needs no composite index. Sort after retrieval.
        from google.cloud.firestore_v1.base_query import FieldFilter
        docs = self.root.collection('events').where(filter=FieldFilter('key', '==', key)).stream()
        return sorted([s.to_dict() for s in docs if s.to_dict()['action'].startswith('disposition:')],
                      key=lambda e: e['ts'], reverse=True)

    def audit(self, limit):
        return [{**s.to_dict(), "seq": s.id} for s in self.root.collection('events').order_by(
            'ts', direction=self.fs.Query.DESCENDING).limit(limit).stream()]

    def record(self, key, e):
        self.root.collection('events').document().create({**e, 'key': key})

    def save_draft(self, key, result, e):
        # Store JSON below the Firestore document size ceiling; fail closed if oversized.
        body = json.dumps(result, default=str)
        if len(body.encode()) > 900_000:
            raise HTTPException(413, "draft exceeds persistent storage limit")
        batch = self.client.batch()
        batch.set(self.root.collection('drafts').document(key), {'body': body})
        batch.create(self.root.collection('events').document(), {**e, 'key': key})
        batch.commit()

    def latest_draft(self, key):
        snap = self.root.collection('drafts').document(key).get()
        return json.loads(snap.to_dict()['body']) if snap.exists else None

    def reserve_draft(self, limit, now=None):
        now = time.time() if now is None else now
        ref = self.root.collection('budgets').document('drafts')
        @self.fs.transactional
        def apply(tx):
            snap = ref.get(transaction=tx)
            times = [t for t in (snap.to_dict().get('times', []) if snap.exists else []) if now-t < 60]
            if len(times) >= limit:
                raise HTTPException(429, "agent draft rate limit reached; try again in a minute")
            tx.set(ref, {'times': times + [now]})
        apply(self.client.transaction())


@lru_cache(maxsize=8)
def _store(backend, location, database):
    if backend == 'firestore':
        return FirestoreCases(location, database)
    if backend == 'sqlite':
        return SQLiteCases(Path(location))
    raise RuntimeError('unsupported case storage backend')


def store():
    from . import config
    backend = os.environ.get('NPI_CASE_BACKEND', 'sqlite')
    if os.environ.get('K_SERVICE') and backend != 'firestore':
        raise HTTPException(503, 'deployed service requires durable Firestore case storage')
    location = os.environ.get('GOOGLE_CLOUD_PROJECT', '') if backend == 'firestore' else str(config.DATA / 'cases.sqlite')
    return _store(backend, location, os.environ.get('NPI_FIRESTORE_DATABASE', 'npi-cases'))
