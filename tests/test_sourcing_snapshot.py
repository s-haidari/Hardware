"""Persist a part's live sourcing (price / stock / lifecycle / lead time) with an
'as of' timestamp, keyed by MPN, in a libs/ sidecar. Today only mfr/MPN/datasheet
persist (as symbol properties); price and stock are lost on relaunch. These snapshots
survive so the detail pane can show "last priced N days ago" instead of nothing.
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402


def _cfg(tmp_path):
    return {"Libs": str(tmp_path)}


def test_save_then_load_roundtrips(tmp_path):
    cfg = _cfg(tmp_path)
    when = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    L.save_sourcing_snapshot(cfg, "STM32F407VGT6",
                             {"unit_price": "$8.12", "stock": 421, "lifecycle": "Active"}, now=when)
    snap = L.sourcing_snapshot_for(cfg, "STM32F407VGT6")
    assert snap["unit_price"] == "$8.12" and snap["stock"] == 421
    assert snap["as_of"] == when.isoformat()


def test_lookup_is_case_insensitive(tmp_path):
    cfg = _cfg(tmp_path)
    L.save_sourcing_snapshot(cfg, "STM32F407VGT6", {"stock": 5})
    assert L.sourcing_snapshot_for(cfg, "stm32f407vgt6")["stock"] == 5


def test_missing_snapshot_is_none(tmp_path):
    assert L.sourcing_snapshot_for(_cfg(tmp_path), "NOPART") is None


def test_second_save_updates_not_duplicates(tmp_path):
    cfg = _cfg(tmp_path)
    L.save_sourcing_snapshot(cfg, "MPN1", {"stock": 1})
    L.save_sourcing_snapshot(cfg, "MPN1", {"stock": 2})
    all_snaps = L.load_sourcing_snapshots(cfg)
    assert len(all_snaps) == 1 and L.sourcing_snapshot_for(cfg, "MPN1")["stock"] == 2


def test_age_label_buckets():
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    iso = lambda d: (now - d).isoformat()
    assert L.snapshot_age_label(iso(timedelta(seconds=10)), now=now) == "just now"
    assert L.snapshot_age_label(iso(timedelta(minutes=5)), now=now) == "5 minutes ago"
    assert L.snapshot_age_label(iso(timedelta(hours=3)), now=now) == "3 hours ago"
    assert L.snapshot_age_label(iso(timedelta(days=2)), now=now) == "2 days ago"
    assert L.snapshot_age_label(iso(timedelta(minutes=1)), now=now) == "1 minute ago"


def test_age_label_bad_input_is_empty():
    assert L.snapshot_age_label("") == ""
    assert L.snapshot_age_label("not-a-date") == ""


def test_corrupt_non_dict_store_resets_to_empty(tmp_path):
    # A truncated / hand-edited file can decode to valid-but-non-object JSON; reads and
    # writes must degrade to an empty store, not crash on None[...] / [...].get.
    (tmp_path / "sourcing_snapshots.json").write_text("null")
    cfg = _cfg(tmp_path)
    assert L.load_sourcing_snapshots(cfg) == {}
    assert L.sourcing_snapshot_for(cfg, "X") is None
    L.save_sourcing_snapshot(cfg, "X", {"stock": 3})       # must not raise
    assert L.sourcing_snapshot_for(cfg, "X")["stock"] == 3
    (tmp_path / "sourcing_snapshots.json").write_text("[1, 2, 3]")
    assert L.load_sourcing_snapshots(cfg) == {}
