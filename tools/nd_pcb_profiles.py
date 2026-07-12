"""nd_pcb_profiles.py — PCB profile engine (Wave 1 · PCB-09).

A *profile* bundles the two independent axes of a board setup:

  * a **fab floor** — an OSH Park fab preset (layer count, min track / clearance /
    drill, stackup) from ``nd_fab_presets``; strictly fabrication, **nets-free**.
  * a **netclass set** — zero or more net classes
    (``nd_netclass_manager.NetClass``).

Splitting them is the whole point of PCB-09: the bare ``OSH Park 4-layer`` /
``OSH Park 2-layer`` profiles carry NO net classes (just the fab floor), while the
``NETDECK`` profile is OSH Park 4-layer **+** the full 19-class vault taxonomy. A
dropdown picks the active profile; users create / save / update / delete their own
on top of the three built-ins.

Built-ins are code-defined and always present. User profiles persist to a JSON
file resolved exactly like ``vault_standard.json`` (next to this module in dev; in
the writable library location under a frozen exe). A user profile may reuse a
built-in's name to **override** it; deleting that override reverts to the built-in.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import nd_fab_presets as fabp
import nd_netclass_manager as ncm

PROFILES_VERSION = 1

# The three seeded profiles (names are also the picker labels).
BARE_OSH_4 = "OSH Park 4-layer"
BARE_OSH_2 = "OSH Park 2-layer"
NETDECK = "NETDECK"
_BUILTIN_NAMES = (BARE_OSH_4, BARE_OSH_2, NETDECK)

# The NetClass fields we persist (mirrors nd_netclass_manager's template format);
# all lengths are canonical mm.
_NETCLASS_FIELDS = (
    "color", "line_style", "wire_thickness", "bus_thickness", "clearance",
    "track_width", "via_diameter", "via_drill", "microvia_diameter",
    "microvia_drill", "diff_pair_width", "diff_pair_gap", "diff_pair_via_gap",
    "priority", "patterns",
)


def _netclass_to_dict(nc) -> dict:
    d = {"name": nc.name}
    for f in _NETCLASS_FIELDS:
        d[f] = getattr(nc, f, None)
    return d


def _netclass_from_dict(d: dict):
    nc = ncm.NetClass(name=d.get("name", "Unnamed"))
    for f in _NETCLASS_FIELDS:
        if d.get(f) is not None:
            setattr(nc, f, d[f])
    if getattr(nc, "patterns", None) is None:
        nc.patterns = []
    return nc


@dataclass
class Profile:
    """One pickable board-setup profile: a fab floor + a (possibly empty) set of
    net classes. ``builtin`` marks the three code-defined seeds (they can be
    overridden but never removed)."""

    name: str
    fab: str                                   # a fab-preset name (built-in or user, see nd_fab_presets)
    netclasses: List["ncm.NetClass"] = field(default_factory=list)
    builtin: bool = False

    @property
    def fab_preset(self):
        """The FabPreset backing this profile's fab floor, or None if unknown. Resolves
        user presets too (get_preset), so a profile can point at a custom fab."""
        return fabp.get_preset(self.fab)

    @property
    def has_nets(self) -> bool:
        """True for a profile that carries net classes (NETDECK); False for a
        bare, nets-free OSH Park fab floor."""
        return bool(self.netclasses)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "fab": self.fab,
            "netclasses": [_netclass_to_dict(nc) for nc in self.netclasses],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        return cls(
            name=d["name"],
            fab=d.get("fab", BARE_OSH_4),
            netclasses=[_netclass_from_dict(x) for x in d.get("netclasses", [])],
        )


def builtin_profiles() -> List[Profile]:
    """The three always-present profiles: two bare (nets-free) OSH Park fab floors
    and NETDECK = OSH Park 4-layer + the full vault netclass set (floors follow the
    4-layer fab)."""
    netdeck_nets = list(
        ncm.create_vault_standard_template(BARE_OSH_4).net_classes.values())
    return [
        Profile(BARE_OSH_4, BARE_OSH_4, [], builtin=True),
        Profile(BARE_OSH_2, BARE_OSH_2, [], builtin=True),
        Profile(NETDECK, BARE_OSH_4, netdeck_nets, builtin=True),
    ]


def profile_from_project(pro_path, name: str, fab: str = BARE_OSH_4) -> Profile:
    """PCB-12: build a Profile from an existing KiCad project's net settings —
    read its net classes (and their patterns) straight out of the `.kicad_pro`.
    The fab floor can't be reverse-engineered reliably, so it defaults to the
    OSH Park 4-layer floor; the user can change it after."""
    mgr = ncm.NetClassManager()
    try:
        mgr.load_from_project(Path(pro_path))
    except Exception:  # noqa: BLE001
        pass
    return Profile(name=name, fab=fab, netclasses=list(mgr.net_classes.values()))


def validate_profile(profile: Profile) -> List[str]:
    """Return a list of human-readable problems (empty == valid)."""
    errs: List[str] = []
    if not (profile.name or "").strip():
        errs.append("Profile name is empty")
    if fabp.get_preset(profile.fab) is None:
        errs.append(f"Unknown fab preset: {profile.fab!r}")
    names = [nc.name for nc in profile.netclasses]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        errs.append(f"Duplicate net class names: {', '.join(dupes)}")
    return errs


# ── persistence (user profiles) ───────────────────────────────────────────────
def _profiles_path() -> Path:
    """Where user profiles are read/written. Under a frozen --onefile exe __file__
    points into the throwaway PyInstaller bundle, so it must write to the user's
    library location; dev keeps it next to the module (matches vault_standard)."""
    import sys
    if getattr(sys, "frozen", False):
        try:
            import LibraryManager as _LM
            loc = _LM.library_location()
            if loc:
                return Path(loc) / "pcb_profiles.json"
        except Exception:  # noqa: BLE001
            pass
        return Path(sys.executable).resolve().parent / "pcb_profiles.json"
    return Path(__file__).resolve().parent / "pcb_profiles.json"


def _load_user_profiles(path: Path) -> List[Profile]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out: List[Profile] = []
    for d in data.get("profiles", []):
        try:
            out.append(Profile.from_dict(d))
        except Exception:  # noqa: BLE001
            pass
    return out


def _write_user_profiles(profiles: List[Profile], path: Path) -> None:
    payload = {"version": PROFILES_VERSION,
               "profiles": [p.to_dict() for p in profiles]}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)                                   # atomic


def load_profiles(path: Optional[Path] = None) -> List[Profile]:
    """Every profile: the built-ins first (canonical order), each replaced by a
    user override of the same name if one exists, then any user-only profiles
    appended. An override keeps the ``builtin`` slot semantics (still can't be
    removed, only reverted)."""
    path = path or _profiles_path()
    user = {p.name: p for p in _load_user_profiles(path)}
    out: List[Profile] = []
    for b in builtin_profiles():
        ov = user.get(b.name)
        if ov is not None:
            ov.builtin = True
            out.append(ov)
        else:
            out.append(b)
    for name, p in user.items():
        if name not in _BUILTIN_NAMES:
            out.append(p)
    return out


def get_profile(name: str, path: Optional[Path] = None) -> Optional[Profile]:
    for p in load_profiles(path):
        if p.name == name:
            return p
    return None


def save_profile(profile: Profile, path: Optional[Path] = None) -> None:
    """Upsert a profile into the user file. Reusing a built-in's name stores an
    override (the built-in stays the fallback)."""
    path = path or _profiles_path()
    user = [p for p in _load_user_profiles(path) if p.name != profile.name]
    user.append(Profile(profile.name, profile.fab, list(profile.netclasses)))
    _write_user_profiles(user, path)


def is_builtin(name: str) -> bool:
    """True for the three code-defined seed profiles (they can be overridden but not
    deleted outright — a delete only reverts a user override back to the seed)."""
    return name in _BUILTIN_NAMES


def has_user_profile(name: str, path: Optional[Path] = None) -> bool:
    """True when something user-saved exists under ``name`` (a pure user profile, or
    a user override of a built-in) — i.e. delete_profile(name) would change something.
    The UI uses this to decide whether a delete confirmation is even warranted."""
    path = path or _profiles_path()
    return any(p.name == name for p in _load_user_profiles(path))


def delete_profile(name: str, path: Optional[Path] = None) -> bool:
    """Delete a user profile, or revert a user override of a built-in. Returns
    False when there is nothing user-saved under ``name`` (a pure built-in can't
    be deleted, only reverted — and it's already at its default)."""
    path = path or _profiles_path()
    user = _load_user_profiles(path)
    if not any(p.name == name for p in user):
        return False
    _write_user_profiles([p for p in user if p.name != name], path)
    return True
