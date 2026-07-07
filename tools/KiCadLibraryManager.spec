# -*- mode: python ; coding: utf-8 -*-
#
# SP1: single source of truth for the frozen build (was stale/ignored — the CI
# workflow now runs `pyinstaller tools/KiCadLibraryManager.spec`).
#
# Bundles, beyond the UI assets:
#   * data/stm32.sqlite  -> data/            (prebuilt in CI; read-only at runtime)
#   * libs/              -> seed/libs/       (seed copied to the user's location)
#   * catalog_assets/    -> seed/catalog_assets/ (if present)
#   * app_secrets.py     (baked Mouser key; hidden import)
# The 19 MB cubemx_db XML is intentionally NOT bundled — it is only needed to
# BUILD the DB, which now happens pre-freeze in CI.
import os

here = os.path.abspath(SPECPATH)           # the tools/ directory
repo_root = os.path.dirname(here)          # the repo root (holds libs/, catalog_assets/)


def _files(src_dir, dest_prefix):
    """(abs_file, dest_dir) for every file in src_dir, one level deep."""
    if not os.path.isdir(src_dir):
        return []
    return [(os.path.join(src_dir, f), dest_prefix)
            for f in os.listdir(src_dir)
            if os.path.isfile(os.path.join(src_dir, f))]


def _tree(src_dir, dest_prefix):
    """(abs_file, dest_dir) for every file under src_dir, preserving structure."""
    out = []
    if not os.path.isdir(src_dir):
        return out
    for root, _dirs, files in os.walk(src_dir):
        rel = os.path.relpath(root, src_dir)
        dest = dest_prefix if rel == '.' else os.path.join(dest_prefix, rel)
        for f in files:
            out.append((os.path.join(root, f), dest))
    return out


# UI assets resolved at runtime via resource_path(...) / _fonts_dir().
datas = []
for asset in ('app_icon.ico', 'app_icon.png', 'caret_down.png', 'caret_up.png',
              'check_dark.png', 'check_light.png', 'icon_sun.png', 'icon_moon.png'):
    p = os.path.join(here, asset)
    if os.path.exists(p):
        datas.append((p, '.'))
datas += _files(os.path.join(here, 'lucide'), 'lucide')   # Lucide SVG icons
datas += _files(os.path.join(here, 'fonts'), 'fonts')     # bundled Inter TTFs

# SP1 read-only bundle: prebuilt STM32 DB (CI writes tools/data/stm32.sqlite).
datas += _files(os.path.join(here, 'data'), 'data')

# SP1 seed: the parts library + catalog assets, copied to the user location on
# first run. Bundled under seed/ so bundle_path('seed/...') finds them.
datas += _tree(os.path.join(repo_root, 'libs'), os.path.join('seed', 'libs'))
datas += _tree(os.path.join(repo_root, 'catalog_assets'), os.path.join('seed', 'catalog_assets'))

# The 3D stack ships its native libs/data via collect_all. collect_all returns
# (src, dest) 2-tuples in the Analysis-INPUT format, so they must be fed through
# Analysis (which normalizes them into 3-tuple TOC entries). Appending them to
# a.binaries / a.datas AFTER Analysis mixes raw 2-tuples into an already
# normalized TOC and breaks EXE()'s normalize_toc under PyInstaller 6.x
# (ValueError: not enough values to unpack (expected 3, got 2)).
from PyInstaller.utils.hooks import collect_all
_extra_bins, _extra_datas, _extra_hidden = [], [], []
for pkg in ('trimesh', 'numpy', 'cascadio'):
    _b, _d, _h = collect_all(pkg)
    _extra_bins += _b
    _extra_datas += _d
    _extra_hidden += _h

a = Analysis(
    [os.path.join(here, 'LibraryManager.py')],
    pathex=[here],
    binaries=_extra_bins,
    datas=datas + _extra_datas,
    hiddenimports=[
        'PyQt5.QtSvg',               # Lucide SVG rendering
        'app_secrets',               # baked Mouser key (SP1)
        'ui.shell',
        'ui.features',
        'ui.features.bench',
        'ui.features.library',
        'ui.features.projects',
        'ui.features.settings',
        'fp_render',                 # lazily imported for previews / catalog
        'kicad_tools',               # KiCad Tools tab (lazily imported)
        'nd_wizard',
        'nd_netclass_manager',
        'nd_project_settings_manager',
        'merge_symbols',
        'stm32_pins_tab',
        'stm32_db',
        'stm32_authority',
        'cascadio',                  # native OpenCASCADE STEP loader (3D)
    ] + _extra_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='KiCad Manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(here, 'app_icon.ico') if os.path.exists(os.path.join(here, 'app_icon.ico')) else None,
)
