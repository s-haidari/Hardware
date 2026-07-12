"""Git — the repository workspace, rebuilt onto the ``kit.workbench`` recipe.

Phase-1 convergence pilot (spec ``docs/superpowers/specs/2026-07-09-phase1-workbench
-recipe.md``): the Git feature is the FIRST consumer of the shared recipe, so the whole
app's per-panel pattern is proven here first. The surface is one ``kit.workbench``
sub-tab that reads, top to bottom:

  * a **colour verdict band** — the repo's state at a glance (branch · clean/N-Changed ·
    ahead/behind chips; an error band when there is no repo or git is not installed);
  * an **active detail region** refreshed IN PLACE — the Changes list (grouped
    staged/modified/untracked), the **Auto-Pull** toggle, and the commit-message field;
  * the single accent **▶ Commit & Sync** primary flow — audit the changed files →
    preview (safe-checked) → stage the checked files → corruption-guard → commit → push →
    a structured report;
  * a **2-col secondary grid** — Stage All / Stage File / Unstage File / Commit /
    Commit & Push / Push / Pull / Sync With Remote / Status·Recent·Integrity·Show-File
    reports; and
  * collapsible **Manage** machinery — Set Up / Initialize Repository.

Two live-sync affordances make it an instrument, not a one-shot panel:

  * a **watchdog** (QFileSystemWatcher on the repo work tree) that debounces a refresh the
    moment files change — a Library drop-in, a KiCad save, a manual edit — with no polling; and
  * the **Auto-Pull** toggle that drives the app-level background auto-pull service the
    shell owns (fast-forward only, via nd_git), so the local copy tracks collaborators
    regardless of the open tab. The toggle seeds from / persists the "AutoPull" pref; the
    timer lives in the shell (GIT-02), not this panel.

All commits route through nd_git's corruption-guarded ``commit`` (it refuses a staged
KiCad file with conflict markers or unbalanced parens) — the same path the Library
drop-in uses — so nothing in the app can push corruption.

Leak discipline (B2 / FIX 7): the detail chrome (Cards, eyebrows, the message field, the
checkbox) is built ONCE in ``detail(...)``; every refresh runs only ``fill(...)``, which
repopulates the Changes card body with the shared *static* vocabulary (``W.static_label``
/ ``W.static_status`` — themed by object name, no per-widget restyle closure). So the
watchdog can fire hundreds of times without growing the global restyle registry, and the
verdict band mutates in place through its single owned restyler (``W.VerdictSlot``).

Headless (render_gate / CI, offscreen Qt): the native watcher is NOT installed, and every
modal (the ▶ preview/report dialogs, the QFileDialog/QInputDialog pickers) short-circuits
via ``_headless()`` so an offscreen drive never blocks — the flow still runs end to end
(``run_populate`` is synchronous headless; ``kit._checkbox_preview`` returns the safe keys;
``kit._report`` logs its one-line summary).
"""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QFileSystemWatcher
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                             QCheckBox, QPushButton)

from .. import widgets as W
from .. import kit as K
from ..util import run_populate, clear_layout, _headless
from .. import feature as F

import nd_git
import LibraryManager as LM

# Cap on directories the watchdog registers. QFileSystemWatcher is non-recursive
# (one watch per directory) and OS-bounded, so a deep tree is walked but capped —
# missing the tail of a pathological tree is fine (commit/pull/tab-entry also refresh);
# an unbounded watch set is not.
_WATCH_DIR_CAP = 512


def _watch_dirs(repo, cap: int = _WATCH_DIR_CAP):
    """Repo directories to watch for live status: the work tree and its subdirectories
    (recursive), skipping .git and any dot-directory, capped at ``cap``. Pure +
    filesystem-only so it is unit-testable without a GUI.

    Uses ``os.walk`` with in-place pruning of dot-directories so those subtrees are never
    descended into — an O(kept dirs) scan rather than materializing every path in the repo.
    Order is irrelevant to the watcher, so no sort is done."""
    import os
    base = Path(repo)
    dirs = [str(base)]
    try:
        for dirpath, subdirs, _files in os.walk(base):
            # Prune dot-directories (including .git) in place so os.walk never descends
            # into them — this is what makes the scan O(kept dirs).
            subdirs[:] = [d for d in subdirs if not d.startswith(".")]
            for d in subdirs:
                if len(dirs) >= cap:
                    return dirs
                dirs.append(os.path.join(dirpath, d))
    except OSError:
        pass
    return dirs


def _clean_state() -> QWidget:
    """A quiet centered empty-state (design-rules §10) for a clean working tree: a muted
    check glyph + one Title-case line. Built from the shared static vocabulary + a
    NEUTRAL-tinted icon pixmap so it registers no restyler AND stays theme-stable (the one
    baked pixmap has no restyler to re-tint on a theme toggle — neutral gray reads on both)."""
    from .. import icons
    w = QWidget()
    col = QVBoxLayout(w); col.setContentsMargins(24, 32, 24, 32); col.setSpacing(0)
    col.addStretch(1)
    icon = QLabel(); icon.setAlignment(Qt.AlignHCenter)
    icon.setPixmap(W.svg_icon(icons.GLYPHS["check"], size=28).pixmap(28, 28))
    col.addWidget(icon, 0, Qt.AlignHCenter)
    col.addSpacing(12)
    line = W.static_label("Working Tree Clean", "dim"); line.setAlignment(Qt.AlignHCenter)
    col.addWidget(line, 0, Qt.AlignHCenter)
    col.addStretch(1)
    return w


# The shared busy gate now lives in the kit (a second workbench needed it); the local
# name is kept because this module coined the pattern and its tests/docs reference it.
_BusyDict = K.BusyDict


def _git_workbench(ctx) -> QWidget:
    """Build the Git kit.workbench sub-surface. Returns the ``host`` body widget (the caller
    wraps it in ``W.scroll_body`` + ``W.Workspace``). Exposes the recipe handles
    (``_verdict`` / ``_region`` / ``_busy`` / ``_run_primary``) plus this feature's own test
    / drive seams (``_snapshot`` / ``_msg`` / ``_auto_cb`` / the secondary handlers /
    ``_btn(text)``)."""
    # Mutable holder for the build-once chrome (the message field, the auto-pull checkbox)
    # and the derived state the refresh + enablement read (the ahead count, the pending
    # message-clear flag, the located action buttons). snapshot() reads ``msg`` from here.
    ui: dict = {"msg": None, "auto_cb": None, "buttons": [], "ahead": 0, "no_upstream": False}
    busy = _BusyDict()

    log = getattr(getattr(ctx, "services", None), "log", None)

    def _log(line):
        if callable(log):
            log(str(line))

    def snapshot() -> dict:
        """The GUI-thread selection dict every worker reads (workers never touch a widget):
        the current repo (re-derived from cfg so an adopted repo takes effect) and the
        trimmed commit-message text."""
        try:
            repo = nd_git.repo_root(Path((ctx.cfg or {}).get("RepoRoot")
                                         or (ctx.cfg or {}).get("SymbolLib") or "."))
        except Exception:  # noqa: BLE001
            repo = None
        msg = ui["msg"].text().strip() if ui.get("msg") is not None else ""
        return {"repo": repo, "msg": msg}

    def _apply_enablement():
        """Reflect the busy gate + the ahead count on the located action buttons: while busy,
        every action + the message field is disabled (no overlap); Push is additionally
        enabled only when there are local commits to push (ahead>0)."""
        on = not busy["on"]
        for text, b in ui.get("buttons", ()):
            try:
                if text == "Push":
                    # Ahead>0 = something to push. no_upstream = ahead is UNKNOWABLE
                    # (ahead_behind() is None with no tracking branch) — keep Push enabled
                    # there so the user can invoke it and get git's actionable "use
                    # --set-upstream" message, instead of a dead greyed button (a
                    # review-confirmed lockout; bare keeps Push always-on).
                    b.setEnabled(on and (ui.get("ahead", 0) > 0 or ui.get("no_upstream", False)))
                else:
                    b.setEnabled(on)
            except RuntimeError:  # a button deleted by a region rebuild — skip it
                pass
        if ui.get("msg") is not None:
            try:
                ui["msg"].setEnabled(on)
            except RuntimeError:
                pass

    busy.on_change = _apply_enablement

    # ── verdict: the repo status colour band (always present — the repo is always the
    #    object of interest) ────────────────────────────────────────────────────────────
    def verdict(snap):
        repo = snap.get("repo")
        # have_git BEFORE the repo check: with git absent, repo_root() also returns None
        # (it shells out to git), so `repo is None` is ALWAYS true on a git-less machine —
        # checked first it would misdiagnose as "No Repository" and misdirect the user to
        # Set Up/Initialize (which would also fail) instead of naming the real cause.
        if not nd_git.have_git():
            return W.VerdictState(kind="err", title="Git Not Installed",
                                  subtitle="Install git to enable version control.")
        if repo is None:
            return W.VerdictState(kind="err", title="No Repository",
                                  subtitle="Set up or initialize a repository below.")
        try:
            # Wrap the status reads: a concurrent index read (a watchdog refresh racing a
            # write) must not raise on the GUI thread — degrade to a neutral band instead.
            st = nd_git.status(repo)
            branch = nd_git.current_branch(repo) or "detached"
            ab = nd_git.ahead_behind(repo)
        except Exception:  # noqa: BLE001
            return W.VerdictState(kind="mut", title="Status Unavailable",
                                  subtitle=Path(repo).as_posix())
        changed = len(st.get("staged", [])) + len(st.get("modified", [])) + len(st.get("untracked", []))
        in_sync = ab is None or (ab[0] == 0 and ab[1] == 0)
        chips = []
        if ab is not None:
            ahead, behind = ab
            if ahead:
                chips.append(("Ahead", str(ahead), "warn"))
            if behind:
                chips.append(("Behind", str(behind), "warn"))
        title = "Clean" if changed == 0 else f"{changed} Changed"
        kind = "ok" if (changed == 0 and in_sync) else "warn"
        return W.VerdictState(kind=kind, title=title, subtitle=f"Branch {branch}", chips=chips)

    # ── detail: chrome built ONCE (Changes card + Auto-Pull + commit message) ──────────
    def detail(snap, handle):
        repo = snap.get("repo")
        body = QWidget()
        col = QVBoxLayout(body); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(14)
        if repo is None:
            # No repo yet: a quiet prompt (the verdict band carries the error). The
            # machinery below (Set Up / Initialize) is how the user recovers.
            col.addWidget(W.eyebrow("Repository"))
            col.addWidget(W.static_label(
                "Not a git repository. Use Set Up or Initialize Repository below.", "dim"))
            ui["msg"] = None
            ui["auto_cb"] = None
            try:
                host._msg = None
                host._auto_cb = None
            except NameError:          # first build: host not yet bound (post-build covers it)
                pass
            return body, (lambda s: None)

        col.addWidget(W.eyebrow("Changes"))
        changes = W.Card(pad=16)
        col.addWidget(changes)

        # Auto-Pull toggle — a LIVE app-level service switch (not a refresh-model widget):
        # it seeds from the persisted pref, commands the shell-owned service on toggle, and
        # syncs from the live broadcast so it never goes stale when toggled elsewhere.
        auto_cb = QCheckBox("Auto-Pull")
        auto_cb.setToolTip("Periodically fast-forward from the remote in the background "
                           "(fast-forward only — never merges or rewrites local work). "
                           "Runs app-wide and persists across launches.")
        auto_cb.setChecked(bool(LM.read_setting("AutoPull", False)))
        auto_cb.toggled.connect(lambda on: ctx.bus.emit("autopull.set_enabled", on))

        def _sync_auto(on):
            auto_cb.blockSignals(True)
            auto_cb.setChecked(bool(on))
            auto_cb.blockSignals(False)

        on_owned = getattr(ctx.bus, "on_owned", None)
        if callable(on_owned):
            # Owner = this chrome widget, so a region rebuild (repo change) drops the old
            # subscription when the old chrome is destroyed — no dead-closure pile-up.
            on_owned("autopull.changed", _sync_auto, body)
        ui["auto_cb"] = auto_cb
        col.addWidget(auto_cb)

        # The commit-message field — the row directly above the ▶ primary. snapshot() reads
        # its text; the flow clears it (via the pending flag) on a successful commit.
        msg = QLineEdit(); msg.setPlaceholderText("Commit message"); msg.setMinimumHeight(34)
        ui["msg"] = msg
        col.addWidget(msg)

        # Re-expose the FRESH chrome on the host seams: a repo adoption rebuilds this
        # region (detail() re-runs), and the once-bound host._msg/_auto_cb would keep
        # pointing at the destroyed first-build widgets (review-confirmed staleness).
        # First build: `host` isn't bound yet — the post-build assignment covers it.
        try:
            host._msg = msg
            host._auto_cb = auto_cb
        except NameError:
            pass

        def fill(s):
            # A successful commit set ``pending_clear`` off-thread (a plain flag, never a
            # cross-thread widget touch); consume it HERE, on the GUI thread, next refresh.
            if ui.pop("pending_clear", False) and ui.get("msg") is not None:
                ui["msg"].clear()
            r = s.get("repo")
            try:
                st = nd_git.status(r) if r else {"clean": True, "staged": [], "modified": [], "untracked": []}
                ab = nd_git.ahead_behind(r) if r else None
            except Exception:  # noqa: BLE001
                st = {"clean": True, "staged": [], "modified": [], "untracked": []}
                ab = None
            ui["ahead"] = ab[0] if ab else 0
            # ab None = no upstream tracking branch (ahead unknowable) — only meaningful
            # with a real repo; keeps Push invocable there (see _apply_enablement).
            ui["no_upstream"] = (ab is None) and (r is not None)

            # Repopulate the Changes card body with the STATIC vocabulary only (no restyler).
            clear_layout(changes.body)
            any_change = False
            for label, files in (("Staged", st.get("staged", [])),
                                 ("Modified", st.get("modified", [])),
                                 ("Untracked", st.get("untracked", []))):
                if not files:
                    continue
                if any_change:
                    changes.body.addSpacing(6)
                any_change = True
                head = QHBoxLayout(); head.setSpacing(8)
                head.addWidget(W.static_label(label, "sub"))
                head.addWidget(W.static_label(f"{len(files)}", "dim"))
                head.addStretch(1)
                changes.body.addLayout(head)
                for f in files[:15]:
                    changes.body.addWidget(W.static_label(str(f), "body"))
                if len(files) > 15:
                    changes.body.addWidget(W.static_label(f"+{len(files) - 15} more", "dim"))
            if not any_change:
                changes.body.addWidget(_clean_state())

            _apply_enablement()   # ahead may have changed → refresh Push enablement

        return body, fill

    # ── the ▶ Commit & Sync primary flow (audit → preview → apply, all headless-safe) ──
    def _cs_audit(snap):
        """OFF-thread: the changed files as preview ops (safe pre-checked). Short-circuits
        (empty ops) with a DISTINCT ``commit_flow.empty`` for no-repo / empty-message /
        clean-tree so the report says exactly why nothing happened."""
        repo = snap.get("repo")
        if repo is None:
            commit_flow.empty = "No repository — set one up first."
            return []
        if not snap.get("msg"):
            commit_flow.empty = "Enter a commit message first, then run Commit & Sync."
            return []
        try:
            st = nd_git.status(repo)
        except Exception as e:  # noqa: BLE001
            commit_flow.empty = f"Could not read status: {e}"
            return []
        seen, files = set(), []
        for f in (list(st.get("staged", [])) + list(st.get("modified", []))
                  + list(st.get("untracked", []))):
            if f not in seen:
                seen.add(f)
                files.append(f)
        if not files:
            commit_flow.empty = "Nothing to commit — the working tree is clean."
            return []
        return [{"key": f, "label": f, "detail": "", "safe": True} for f in files]

    def _cs_intro(snap, ops):
        return (f"Commit message: {snap.get('msg')!r}\n"
                "Stage + commit the checked files, corruption-guard, then push:")

    def _cs_apply(snap, keys):
        """OFF-thread: stage the checked files → corruption-guard → commit → push → a
        structured report. Never reports a push failure as a commit failure (the commit's
        work is safe locally; Push recovers it). Sets ``pending_clear`` so the GUI-thread
        fill clears the message field on the post-flow refresh."""
        repo, m = snap.get("repo"), snap.get("msg")
        done, errors = [], []
        for f in keys:
            v = nd_git.stage(repo, f)
            if getattr(v, "ok", True):
                done.append(f"staged {f}")
            else:
                errors.append(f"stage {f}: {getattr(v, 'message', '')}")
        bad = nd_git.guard_no_corrupt_kicad(repo)
        if bad:
            return {"summary": "Commit BLOCKED — corrupt KiCad staged.",
                    "done": done,
                    "missing": [{"item": Path(p).name, "why": why,
                                 "how_to_fix": "Fix the merge markers / balance the parens, then retry."}
                                for p, why in bad]}
        ok, info = nd_git.commit(repo, m)
        if not ok:
            errors.append(f"commit: {info}")
            return {"summary": "Commit failed.", "done": done, "errors": errors}
        done.append(f"committed {info}")
        try:
            res = nd_git.push(repo)
            pushed_ok = bool(getattr(res, "ok", False))
            if pushed_ok:
                done.append("pushed")
            else:
                errors.append(f"push FAILED: {getattr(res, 'message', '')} — commit succeeded, push manually")
        except Exception as e:  # noqa: BLE001
            pushed_ok = False
            errors.append(f"push FAILED: {e} — commit succeeded, push manually")
        ui["pending_clear"] = True
        return {"summary": f"Committed {info}" + (" + pushed" if pushed_ok else " — PUSH FAILED"),
                "done": done, "errors": errors}

    # NB: no "&" in a button label — Qt reads a single "&" as a mnemonic marker (it
    # underlines the next char, e.g. "Commit & Sync" renders as "Commit_Sync"), and W.btn
    # doesn't escape. "and" reads clean in the button AND in the report title / log line
    # (the flow's label is reused as both).
    commit_flow = K.PrimaryFlow(
        label="▶ Commit and Sync", audit=_cs_audit, intro=_cs_intro, apply=_cs_apply,
        tip="Preview → stage the checked files → corruption-guard → commit → push, in one action",
        empty="Nothing to commit — the working tree is clean.")

    # ── secondary + machinery op runners (busy-gated, off-thread, then refresh) ─────────
    def _run_op(label, work, *, busy_label=None):
        """Run a mutating op OFF-thread, busy-gated, then log its one-line result + refresh
        (card + verdict + enablement). ``work()`` returns the human line."""
        if busy["on"]:
            return
        busy["on"] = True

        def done(line, ok):
            busy["on"] = False
            if line:
                _log(line)
            host._region.handle.refresh()   # the recipe's combined refresh (card + verdict)

        run_populate(ctx, work, done, busy=busy_label or f"{label}…")

    def _report_op(label, work, *, mutating=False, busy_label=None):
        """Run an op OFF-thread and show its structured result via ``kit._report`` (headless:
        logs the summary). ``mutating`` ops take the busy gate + refresh after; read-only
        reports (Status / Recent / Integrity / Show File) do not."""
        if mutating and busy["on"]:
            return
        if mutating:
            busy["on"] = True

        def done(result, ok):
            if mutating:
                busy["on"] = False
            K._report(host, label, result if ok else {"errors": ["operation failed"]}, log=log)
            if mutating:
                host._region.handle.refresh()

        run_populate(ctx, work, done, busy=busy_label or f"{label}…")

    def _stage_all():
        repo = snapshot()["repo"]
        if repo is None:
            _log("No repository — set one up first."); return

        def work():
            r = nd_git.stage_all(repo)
            return "Staged all changes." if getattr(r, "ok", False) else f"Stage all failed: {getattr(r, 'message', '')}"

        _run_op("Stage All", work)

    def _commit(push=False):
        snap = snapshot()
        repo, m = snap["repo"], snap["msg"]
        if repo is None:
            _log("No repository — set one up first."); return
        if not m:
            # Don't fabricate a message — the backend rejects an empty one; routing around
            # it pollutes history. Prompt and focus the field instead.
            _log("Enter a commit message.")
            if ui.get("msg") is not None:
                ui["msg"].setFocus()
            return

        def work():
            nd_git.stage_all(repo)          # this flat action means "commit everything I see"
            ok, info = nd_git.commit(repo, m)
            if not ok:
                return f"Commit failed: {info}"
            # Clear on COMMIT success, push outcome irrelevant (deliberate): the message
            # now lives in the commit, and the recovery for a failed push is the Push
            # button — never a re-commit — so a kept message would only invite committing
            # the same text twice. (bare's ▶ flow clears the same way.)
            ui["pending_clear"] = True      # consumed on the GUI thread next refresh
            if push:
                try:
                    res = nd_git.push(repo)
                    return ("Committed and pushed." if getattr(res, "ok", False)
                            else f"Committed; push failed: {getattr(res, 'message', '')}")
                except Exception as e:  # noqa: BLE001
                    return f"Committed; push failed: {e}"
            return "Committed."

        _run_op("Commit", work)

    def _push():
        repo = snapshot()["repo"]
        if repo is None:
            _log("No repository — set one up first."); return

        def work():
            try:
                res = nd_git.push(repo)
            except Exception as e:  # noqa: BLE001
                return f"Push failed: {e}"
            return "Pushed." if getattr(res, "ok", False) else f"Push failed: {getattr(res, 'message', '')}"

        _run_op("Push", work)

    def _pull():
        repo = snapshot()["repo"]
        if repo is None:
            _log("No repository — set one up first."); return

        def work():
            res = nd_git.pull_ff_only(repo)
            if getattr(res, "ok", False):
                return "Repository is up to date."
            # Surface git's REAL first line (e.g. "There is no tracking information for
            # the current branch") — a hardcoded not-a-fast-forward guess misdiagnoses a
            # missing upstream / auth failure (review-confirmed).
            why = (getattr(res, "message", "") or "").strip().splitlines()
            return ("Pull skipped: " + why[0] if why
                    else "Pull skipped: not a fast-forward, or the remote is unreachable.")

        _run_op("Pull", work)

    def _sync_remote():
        repo = snapshot()["repo"]
        if repo is None:
            _log("No repository — set one up first."); return

        def work():
            ab = nd_git.ahead_behind(repo)
            if ab is None:
                return {"summary": "No upstream tracking branch.",
                        "missing": [{"item": "upstream", "why": "this branch has no upstream",
                                     "how_to_fix": "Push once with -u, or set the upstream in git."}]}
            ahead, behind = ab
            if ahead == 0 and behind == 0:
                return {"summary": "In sync — nothing to pull or push."}
            done, errors = [], []
            if behind > 0:
                res = nd_git.pull_ff_only(repo)
                (done if getattr(res, "ok", False) else errors).append(
                    f"pulled {behind} commit(s)" if getattr(res, "ok", False)
                    else f"pull FAILED: {getattr(res, 'message', '')}")
            if ahead > 0 and not errors:
                res = nd_git.push(repo)
                (done if getattr(res, "ok", False) else errors).append(
                    f"pushed {ahead} commit(s)" if getattr(res, "ok", False)
                    else f"push FAILED: {getattr(res, 'message', '')}")
            return {"summary": f"Sync: was {ahead} ahead / {behind} behind.",
                    "done": done, "errors": errors}

        _report_op("Sync With Remote", work, mutating=True)

    def _status_report():
        repo = snapshot()["repo"]
        if repo is None:
            _log("No repository — set one up first."); return

        def work():
            s = nd_git.status(repo)
            br = nd_git.current_branch(repo)
            ab = nd_git.ahead_behind(repo)
            missing = ([{"item": f, "why": "modified, not staged",
                         "how_to_fix": "Commit & Sync stages it for you."}
                        for f in s.get("modified", [])[:60]]
                       + [{"item": f, "why": "untracked", "how_to_fix": "Stage it to include it."}
                          for f in s.get("untracked", [])[:60]])
            return {"summary": f"branch {br or '(detached)'} · {'clean' if s['clean'] else 'dirty'} · "
                               f"{len(s['staged'])} staged / {len(s['modified'])} modified / "
                               f"{len(s['untracked'])} untracked"
                               + (f" · ahead {ab[0]} behind {ab[1]}" if ab else " · no upstream"),
                    "done": [f"staged: {f}" for f in s.get("staged", [])[:60]], "missing": missing}

        _report_op("Working Tree Status", work)

    def _recent_commits():
        repo = snapshot()["repo"]
        if repo is None:
            _log("No repository — set one up first."); return

        def work():
            commits = nd_git.recent_commits(repo, 25)
            return {"summary": f"{len(commits)} recent commit(s), newest first:",
                    "done": [f"{c['ref']}  {c['subject']}  ({c['when']})" for c in commits]}

        _report_op("Recent Commits", work)

    def _integrity_scan():
        repo = snapshot()["repo"]
        if repo is None:
            _log("No repository — set one up first."); return

        def work():
            bad = nd_git.find_corrupt_kicad_files(repo)
            if not bad:
                return {"summary": "Working tree is clean of corrupt KiCad files."}
            return {"summary": f"{len(bad)} corrupt KiCad file(s) found.",
                    "missing": [{"item": Path(p).as_posix(), "why": why,
                                 "how_to_fix": "Fix the merge markers / balance the parens, or discard "
                                               "the file — a commit is blocked until it's clean."}
                                for p, why in bad]}

        _report_op("Integrity Scan", work)

    def _show_file(path=None):
        repo = snapshot()["repo"]
        if repo is None:
            _log("No repository — set one up first."); return
        rel = path
        if rel is None:
            if _headless():
                _log("Show File @ HEAD is unavailable in a headless run."); return
            from PyQt5.QtWidgets import QInputDialog
            text, ok = QInputDialog.getText(host, "Show File @ HEAD", "Repo-relative path:")
            if not ok or not text.strip():
                return
            rel = text.strip()

        def work():
            res = nd_git.show(repo, "HEAD", rel)
            if not getattr(res, "ok", False):
                return {"errors": [getattr(res, "message", "not found")]}
            out = getattr(res, "out", None) or getattr(res, "message", "") or ""
            return {"summary": f"{rel} @ HEAD:", "done": str(out).splitlines()[:300]}

        _report_op(f"{rel} @ HEAD", work)

    def _stage_file(path=None):
        repo = snapshot()["repo"]
        if repo is None:
            _log("No repository — set one up first."); return
        p = path
        if p is None:
            if _headless():
                _log("Stage File is unavailable in a headless run."); return
            from PyQt5.QtWidgets import QFileDialog
            p, _ = QFileDialog.getOpenFileName(host, "Stage a file", str(repo))
            if not p:
                return

        def work():
            r = nd_git.stage(repo, p)
            return "Staged file." if getattr(r, "ok", False) else f"Stage failed: {getattr(r, 'message', '')}"

        _run_op("Stage File", work)

    def _unstage_file(path=None):
        repo = snapshot()["repo"]
        if repo is None:
            _log("No repository — set one up first."); return
        p = path
        if p is None:
            if _headless():
                _log("Unstage File is unavailable in a headless run."); return
            from PyQt5.QtWidgets import QFileDialog
            p, _ = QFileDialog.getOpenFileName(host, "Unstage a file", str(repo))
            if not p:
                return

        def work():
            r = nd_git.unstage(repo, p)
            return "Unstaged file." if getattr(r, "ok", False) else f"Unstage failed: {getattr(r, 'message', '')}"

        _run_op("Unstage File", work)

    # ── machinery (collapsed): adopt an existing repo / initialize a new one ────────────
    def _adopt(root):
        """Persist ``root`` as the active repo (cfg + config.json) so every dependent action
        re-derives from it, then rebuild the region so the rebuilt chrome reflects the new
        repo. GUI thread (touches cfg + widgets)."""
        root = Path(root)
        if not LM.save_repo_root(ctx.cfg, root):
            K._report(host, "Repository", {"errors": [
                f"could not persist repo root {root.as_posix()} (not writable, or config.json unsaveable)"]},
                log=log)
            return False
        _log(f"Repository set: {root.as_posix()}")
        host._region.handle.rebuild()   # deferred: re-derive chrome from the new cfg
        host._refresh()                 # verdict/enablement now (the rebuild is deferred)
        host._install_watchdog()        # re-point the live watcher at the adopted repo
        return True

    def _set_up_repo(path=None):
        p = path
        if p is None:
            if _headless():
                _log("Set Up Repository is unavailable in a headless run."); return
            from PyQt5.QtWidgets import QInputDialog
            snap = snapshot()
            initial = ""
            if snap["repo"] is not None:
                initial = Path(snap["repo"]).as_posix()
            elif (ctx.cfg or {}).get("RepoRoot"):
                initial = str(ctx.cfg["RepoRoot"])
            text, ok = QInputDialog.getText(host, "Set Up Repository",
                                            "Path to an existing git repo:", text=initial)
            if not ok or not text.strip():
                return
            p = text.strip()
        v = nd_git.set_repo(Path(p))
        if not v.ok:
            K._report(host, "Set Up Repository", {"errors": [v.reason or "not a usable repo path"]}, log=log)
            return
        if not v.is_repo:
            K._report(host, "Set Up Repository",
                      {"summary": f"{Path(p).as_posix()} is not a git repository yet — "
                                  "use Initialize Repository to create one there."}, log=log)
            return
        _adopt(v.root or Path(p))

    def _initialize_repo(path=None):
        p = path
        if p is None:
            if _headless():
                _log("Initialize Repository is unavailable in a headless run."); return
            from PyQt5.QtWidgets import QInputDialog
            text, ok = QInputDialog.getText(host, "Initialize Repository",
                                            "Path to initialize a new git repo in:")
            if not ok or not text.strip():
                return
            p = text.strip()
        r = nd_git.init_repo(Path(p))
        if not r.ok:
            K._report(host, "Initialize Repository", {"errors": [r.message]}, log=log)
            return
        root = nd_git.repo_root(Path(p)) or Path(p)
        _adopt(root)

    # ── assemble the workbench ─────────────────────────────────────────────────────────
    secondary = [
        K.action("Stage All", _stage_all, tip="git add -A — stage every change in the work tree"),
        K.action("Stage File…", lambda: _stage_file(), tip="Stage a single file"),
        K.action("Unstage File…", lambda: _unstage_file(), tip="Remove a file from the index"),
        K.action("Commit", lambda: _commit(False), tip="Stage everything shown, then commit"),
        K.action("Commit and Push", lambda: _commit(True), tip="Commit then push to the remote"),
        K.action("Push", _push, tip="Push local commits to the remote"),
        K.action("Pull", _pull, tip="Fetch and fast-forward the current branch"),
        K.action("Sync With Remote", _sync_remote, tip="Fast-forward pull if behind, then push if ahead"),
        K.action("Status Report…", _status_report, tip="A full working-tree status report"),
        K.action("Recent Commits…", _recent_commits, tip="The 25 most recent commits"),
        K.action("Integrity Scan…", _integrity_scan, tip="Scan the work tree for corrupt KiCad files"),
        K.action("Show File @ HEAD…", lambda: _show_file(), tip="View a file as committed at HEAD"),
    ]
    machinery = [
        K.action("Set Up Repository…", lambda: _set_up_repo(), tip="Point the app at an existing git repo"),
        K.action("Initialize Repository…", lambda: _initialize_repo(), tip="Create a new git repo and adopt it"),
    ]

    host = K.workbench(ctx, title="Git", snapshot=snapshot, verdict=verdict, detail=detail,
                       primary=commit_flow, secondary=secondary, machinery=machinery, busy=busy)

    # Locate the action buttons for the busy / ahead enablement (the recipe builds them
    # internally). Collapsible chevrons are skipped — the busy gate never disables them.
    ui["buttons"] = [(b.text(), b) for b in host.findChildren(QPushButton)
                     if not b.text().startswith(("▸", "▾"))]

    # A guarded ▶ so a re-entry (a test re-drive, or a click while a secondary op runs) can't
    # start a second flow. The real ▶ button is also disabled while busy (via the busy gate).
    _raw_run = host._run_primary

    def _run_primary():
        if busy["on"]:
            return
        _raw_run()

    host._run_primary = _run_primary

    # Test / drive seams + the handles the old panel exposed, rewritten to the recipe.
    host._snapshot = snapshot
    host._msg = ui.get("msg")
    host._auto_cb = ui.get("auto_cb")
    host._commit = _commit
    host._push = _push
    host._pull = _pull
    host._stage_all = _stage_all
    host._sync_remote = _sync_remote
    host._status_report = _status_report
    host._recent_commits = _recent_commits
    host._integrity_scan = _integrity_scan
    host._show_file = _show_file
    host._stage_file = _stage_file
    host._unstage_file = _unstage_file
    host._set_up_repo = _set_up_repo
    host._init_repo = _initialize_repo
    host._btn = lambda text: next((b for t, b in ui.get("buttons", ()) if t == text), None)

    _apply_enablement()   # initial enablement (Push gated on the initial ahead count)

    # ── live watchdog (skipped headless: native watcher off-screen) ─────────────────────
    # The background auto-pull TIMER lives in the shell (app-level, GIT-02); this panel only
    # installs the local file watchdog that refreshes the visible status the moment the work
    # tree changes. QFileSystemWatcher is non-recursive, so watch the work tree + its
    # subdirectories (bounded), skipping .git; a create/delete/rename raises directoryChanged.
    def _install_watchdog():
        """(Re)point the watchdog at the CURRENT repo. Called at build AND from _adopt —
        a repo adoption rebuilds only the region, never this builder, so a one-shot
        install would leave the watcher absent (no-repo start → adopt) or watching the
        OLD tree forever (repo switch) — both review-confirmed. Skipped headless."""
        if _headless():
            return
        old = getattr(host, "_watcher", None)
        if old is not None:
            try:
                old.deleteLater()               # drops its watches + directoryChanged wiring
            except RuntimeError:
                pass
            host._watcher = None
        repo = snapshot()["repo"]
        if repo is None:
            return
        watcher = QFileSystemWatcher(host)
        watcher.addPaths(_watch_dirs(repo))
        if getattr(host, "_debounce", None) is None:
            debounce = QTimer(host); debounce.setSingleShot(True); debounce.setInterval(500)
            debounce.timeout.connect(host._region.handle.refresh)
            host._debounce = debounce
        watcher.directoryChanged.connect(lambda _p: host._debounce.start())
        host._watcher = watcher

    host._install_watchdog = _install_watchdog
    _install_watchdog()

    return host


class GitFeature(F.Feature):
    id = "git"
    title = "Git"
    order = 50                          # after Bench (30), before Settings (900)

    def build(self, ctx: F.Context) -> QWidget:
        # Single-panel Workspace: the page title reads "Git" and the sub-tab bar is hidden
        # (as the merged Library does). The one panel is the Git workbench recipe.
        return W.Workspace(ctx, "Git", [("Repository", lambda c: W.scroll_body(_git_workbench(c)))])


F.register(GitFeature())
