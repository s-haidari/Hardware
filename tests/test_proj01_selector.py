"""PROJ-01: the project selector must disambiguate identically-named projects by
path, and selection must resolve to the exact project (not the first name match)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ui.features.projects import ProjectsState  # noqa: E402


def _state(paths):
    st = ProjectsState({})
    st.projects = [Path(p) for p in paths]
    st.project = st.projects[0] if st.projects else None
    return st


def test_unique_name_shows_name_only():
    st = _state(["/repo/Widget", "/repo/Gadget"])
    assert st.labels() == ["Widget", "Gadget"]


def test_duplicate_names_are_disambiguated_by_parent():
    st = _state(["/a/Master", "/b/Master"])
    labels = st.labels()
    assert labels[0] != labels[1]
    assert all("Master" in lab for lab in labels)
    assert "a" in labels[0] and "b" in labels[1]


def test_residual_collision_falls_back_to_full_path():
    # Same name AND same parent name -> disambiguate by full path.
    st = _state(["/x/dup/Master", "/y/dup/Master"])
    labels = st.labels()
    assert labels[0] != labels[1]
    assert labels[0] == "/x/dup/Master" and labels[1] == "/y/dup/Master"


def test_select_index_resolves_exact_project():
    st = _state(["/a/Master", "/b/Master"])
    st.select_index(1)
    assert st.project == Path("/b/Master")


def test_select_index_fires_refreshers():
    st = _state(["/a/Master", "/b/Master"])
    hits = []
    st.on_change(lambda: hits.append(1))
    st.select_index(1)
    assert hits == [1]
