"""Wave 1 · PCB-09 — the PCB profile engine (nd_pcb_profiles).

A profile = a fab floor (OSH Park preset, nets-free) + a netclass set. The two
bare OSH Park profiles carry NO nets; NETDECK = OSH Park 4-layer + the full vault
netclass set. User profiles persist to a JSON file; a user profile may override a
built-in by name, and deleting the override reverts to the built-in.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import nd_pcb_profiles as P  # noqa: E402
import nd_fab_presets as fabp  # noqa: E402
import nd_netclass_manager as ncm  # noqa: E402


# ── built-ins: the two axes are split ─────────────────────────────────────────
def test_builtins_are_the_three_seeds():
    names = [p.name for p in P.builtin_profiles()]
    assert names == [P.BARE_OSH_4, P.BARE_OSH_2, P.NETDECK]
    assert all(p.builtin for p in P.builtin_profiles())


def test_bare_osh_profiles_are_nets_free():
    for name in (P.BARE_OSH_4, P.BARE_OSH_2):
        prof = P.get_profile(name, path=_missing(name))
        assert prof.netclasses == []
        assert prof.has_nets is False


def test_netdeck_is_osh4_plus_full_netclass_set():
    netdeck = next(p for p in P.builtin_profiles() if p.name == P.NETDECK)
    assert netdeck.fab == P.BARE_OSH_4                       # fab floor = OSH Park 4-layer
    assert netdeck.has_nets is True
    template = ncm.create_vault_standard_template(P.BARE_OSH_4)
    assert len(netdeck.netclasses) == len(template.net_classes)
    assert len(netdeck.netclasses) >= 15                    # the full vault taxonomy


def test_bare_profile_resolves_its_fab_preset():
    osh4 = next(p for p in P.builtin_profiles() if p.name == P.BARE_OSH_4)
    assert osh4.fab_preset is fabp.PRESETS[P.BARE_OSH_4]
    assert osh4.fab_preset.layers == 4


# ── serialization round-trip ──────────────────────────────────────────────────
def test_profile_roundtrips_through_dict():
    netdeck = next(p for p in P.builtin_profiles() if p.name == P.NETDECK)
    back = P.Profile.from_dict(netdeck.to_dict())
    assert back.name == netdeck.name and back.fab == netdeck.fab
    assert [n.name for n in back.netclasses] == [n.name for n in netdeck.netclasses]
    # a representative numeric field survives the round-trip
    a = {n.name: n for n in netdeck.netclasses}
    b = {n.name: n for n in back.netclasses}
    for name in a:
        assert b[name].clearance == a[name].clearance
        assert b[name].track_width == a[name].track_width
        assert b[name].patterns == a[name].patterns


# ── user CRUD + persistence ───────────────────────────────────────────────────
def _missing(_name=""):
    # a path guaranteed not to exist → load returns built-ins only
    return Path("/nonexistent/pcb_profiles.json")


def test_save_then_load_user_profile(tmp_path):
    path = tmp_path / "pcb_profiles.json"
    custom = P.Profile("My Board", P.BARE_OSH_2, [])
    P.save_profile(custom, path=path)
    names = [p.name for p in P.load_profiles(path=path)]
    assert names[:3] == [P.BARE_OSH_4, P.BARE_OSH_2, P.NETDECK]   # built-ins first
    assert "My Board" in names                                   # user profile appended
    got = P.get_profile("My Board", path=path)
    assert got.fab == P.BARE_OSH_2 and got.has_nets is False


def test_user_can_override_a_builtin(tmp_path):
    path = tmp_path / "pcb_profiles.json"
    # Override NETDECK to sit on the 2-layer fab.
    P.save_profile(P.Profile(P.NETDECK, P.BARE_OSH_2, []), path=path)
    profiles = P.load_profiles(path=path)
    assert [p.name for p in profiles].count(P.NETDECK) == 1       # not duplicated
    netdeck = next(p for p in profiles if p.name == P.NETDECK)
    assert netdeck.fab == P.BARE_OSH_2                            # the override won
    assert netdeck.builtin is True                               # still a built-in slot
    assert len(profiles) == 3                                    # no extra profile added


def test_delete_user_profile(tmp_path):
    path = tmp_path / "pcb_profiles.json"
    P.save_profile(P.Profile("Scratch", P.BARE_OSH_4, []), path=path)
    assert P.delete_profile("Scratch", path=path) is True
    assert "Scratch" not in [p.name for p in P.load_profiles(path=path)]


def test_delete_pure_builtin_returns_false(tmp_path):
    path = tmp_path / "pcb_profiles.json"                         # no user file yet
    assert P.delete_profile(P.NETDECK, path=path) is False       # can't delete a built-in


# ── is_builtin / has_user_profile: the UI uses these to gate the delete confirm ──
def test_is_builtin_flags_the_three_seeds_only():
    assert P.is_builtin(P.NETDECK) and P.is_builtin(P.BARE_OSH_4) and P.is_builtin(P.BARE_OSH_2)
    assert not P.is_builtin("Scratch")


def test_has_user_profile_tracks_deletability(tmp_path):
    path = tmp_path / "pcb_profiles.json"
    assert P.has_user_profile("Scratch", path=path) is False      # nothing saved yet
    P.save_profile(P.Profile("Scratch", P.BARE_OSH_4, []), path=path)
    assert P.has_user_profile("Scratch", path=path) is True       # now deletable
    # a pure built-in with no override is not "user" (delete would be a no-op)
    assert P.has_user_profile(P.NETDECK, path=path) is False
    P.save_profile(P.Profile(P.NETDECK, P.BARE_OSH_4, []), path=path)  # user override
    assert P.has_user_profile(P.NETDECK, path=path) is True       # override IS revertible


def test_delete_reverts_a_builtin_override(tmp_path):
    path = tmp_path / "pcb_profiles.json"
    P.save_profile(P.Profile(P.NETDECK, P.BARE_OSH_2, []), path=path)   # override
    assert P.delete_profile(P.NETDECK, path=path) is True              # revert
    netdeck = next(p for p in P.load_profiles(path=path) if p.name == P.NETDECK)
    assert netdeck.fab == P.BARE_OSH_4                                 # back to code default
    assert netdeck.has_nets is True                                   # full nets restored


# ── validation ────────────────────────────────────────────────────────────────
def test_validate_flags_unknown_fab():
    errs = P.validate_profile(P.Profile("X", "Made Up Fab", []))
    assert any("fab" in e.lower() for e in errs)


def test_validate_flags_duplicate_netclass_names():
    dupes = [ncm.NetClass(name="GND"), ncm.NetClass(name="GND")]
    errs = P.validate_profile(P.Profile("X", P.BARE_OSH_4, dupes))
    assert any("duplicate" in e.lower() for e in errs)


def test_validate_accepts_a_builtin():
    netdeck = next(p for p in P.builtin_profiles() if p.name == P.NETDECK)
    assert P.validate_profile(netdeck) == []
