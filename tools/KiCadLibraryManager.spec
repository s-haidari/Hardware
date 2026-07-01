# -*- mode: python ; coding: utf-8 -*-
import os

here = os.path.abspath(SPECPATH)


def _tree(rel):
    src = os.path.join(here, rel)
    if not os.path.isdir(src):
        return []
    return [(os.path.join(src, f), rel) for f in os.listdir(src)
            if os.path.isfile(os.path.join(src, f))]


# Assets resolved at runtime via resource_path(...).
datas = []
for asset in ('app_icon.ico', 'app_icon.png', 'caret_down.png', 'caret_up.png',
              'check_dark.png', 'check_light.png', 'icon_sun.png', 'icon_moon.png'):
    p = os.path.join(here, asset)
    if os.path.exists(p):
        datas.append((p, '.'))
datas += _tree('lucide')   # Lucide SVG icons  -> lucide/*.svg
datas += _tree('fonts')    # bundled Inter TTFs -> fonts/*.ttf

a = Analysis(
    [os.path.join(here, 'LibraryManager.py')],
    pathex=[here],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'PyQt5.QtSvg',               # Lucide SVG rendering
        'fp_render',                 # lazily imported for previews / catalog
        'kicad_tools',               # KiCad Tools tab (lazily imported)
        'nd_wizard',
        'nd_netclass_manager',
        'nd_project_settings_manager',
        'merge_symbols',
    ],
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
    name='KiCadLibraryManager',
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
