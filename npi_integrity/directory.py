"""User-scoped, NPI-only synthetic pilot directory storage.

This is separate from the immutable detection warehouse. No patient or claim
rows enter this store. The deployed service requires durable Firestore.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import HTTPException

from . import config


def owner_key(actor: str) -> str:
    return hashlib.sha256(actor.strip().lower().encode()).hexdigest()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteDirectory:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS directories (owner TEXT PRIMARY KEY, body TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS directory_events (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, body TEXT NOT NULL)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def get(self, owner):
        with self.connect() as conn:
            row = conn.execute("SELECT body FROM directories WHERE owner=?", [owner]).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, owner, document, action):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO directories VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET body=excluded.body",
                         [owner, json.dumps(document)])
            conn.execute("INSERT INTO directory_events(owner,body) VALUES (?,?)", [owner, json.dumps({"action": action, "at": utcnow()})])

    def delete(self, owner):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM directories WHERE owner=?", [owner])
            conn.execute("INSERT INTO directory_events(owner,body) VALUES (?,?)", [owner, json.dumps({"action": "delete", "at": utcnow()})])


class FirestoreDirectory:
    def __init__(self, project, database):
        from google.cloud import firestore
        self.client = firestore.Client(project=project, database=database)
        self.root = self.client.collection("npi_integrity").document("v1")

    def ref(self, owner):
        return self.root.collection("directories").document(owner)

    def get(self, owner):
        snap = self.ref(owner).get()
        return snap.to_dict() if snap.exists else None

    def put(self, owner, document, action):
        batch = self.client.batch()
        batch.set(self.ref(owner), document)
        batch.create(self.root.collection("directory_events").document(),
                     {"owner": owner, "action": action, "at": utcnow()})
        batch.commit()

    def delete(self, owner):
        batch = self.client.batch()
        batch.delete(self.ref(owner))
        batch.create(self.root.collection("directory_events").document(),
                     {"owner": owner, "action": "delete", "at": utcnow()})
        batch.commit()


@lru_cache(maxsize=8)
def _store(backend, location, database):
    if backend == "firestore":
        return FirestoreDirectory(location, database)
    if backend == "sqlite":
        return SQLiteDirectory(config.DATA / "directory.sqlite")
    raise RuntimeError("unsupported directory storage backend")


def store():
    backend = os.environ.get("NPI_CASE_BACKEND", "sqlite")
    if os.environ.get("K_SERVICE") and backend != "firestore":
        raise HTTPException(503, "deployed directory requires durable Firestore")
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    return _store(backend, project, os.environ.get("NPI_FIRESTORE_DATABASE", "npi-cases"))
