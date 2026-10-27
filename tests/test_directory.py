"""Saved pilot directories remain owner-scoped and rechecks report real changes."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from npi_integrity import api
from npi_integrity.directory import SQLiteDirectory, owner_key


def request(actor):
    return SimpleNamespace(state=SimpleNamespace(actor=actor))


def test_directory_owner_isolation_and_delete(tmp_path, monkeypatch):
    storage = SQLiteDirectory(tmp_path / "directory.sqlite")
    monkeypatch.setattr(api, "directory_store", lambda: storage)
    monkeypatch.setattr(api, "check_roster", lambda body: {"results": [
        {"npi": n, "name": "Sample", "label": "No flagged signals", "reason": "Synthetic check"}
        for n in body.npis]})
    monkeypatch.setattr(api, "directory_as_of", lambda: "2026-10-06")
    npi = "1234567893"
    a, b = request("a@texmed.us"), request("b@texmed.us")
    assert api.get_directory(a)["saved"] is False
    saved = api.save_directory(a, api.RosterInput(npis=[npi]))
    assert saved["saved"] and saved["as_of"] == "2026-10-06"
    assert api.get_directory(b)["saved"] is False
    assert api.get_directory(a)["npis"] == [npi]
    api.delete_directory(a)
    assert api.get_directory(a)["saved"] is False
    assert owner_key("A@texmed.us") == owner_key("a@texmed.us")


def test_directory_recheck_change_and_missing(tmp_path, monkeypatch):
    storage = SQLiteDirectory(tmp_path / "directory.sqlite")
    monkeypatch.setattr(api, "directory_store", lambda: storage)
    monkeypatch.setattr(api, "directory_as_of", lambda: "2026-10-06")
    labels = ["No flagged signals", "Needs attention"]
    monkeypatch.setattr(api, "check_roster", lambda body: {"results": [
        {"npi": body.npis[0], "name": "Sample", "label": labels.pop(0), "reason": "Synthetic check"}]})
    who = request("a@texmed.us")
    with pytest.raises(HTTPException) as exc:
        api.recheck_directory(who)
    assert exc.value.status_code == 404
    api.save_directory(who, api.RosterInput(npis=["1234567893"]))
    updated = api.recheck_directory(who)
    assert updated["changes"] == [{"npi": "1234567893", "before": "No flagged signals", "after": "Needs attention"}]
    assert api.get_directory(who)["results"][0]["label"] == "Needs attention"
