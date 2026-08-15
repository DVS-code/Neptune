# -*- mode: python ; coding: utf-8 -*-
import os

datas = []
if os.path.isdir('assets'):
    datas.append(('assets', 'assets'))

icon = 'assets/neptune.ico' if os.path.isfile('assets/neptune.ico') else None

analysis = Analysis(
    ['neptune/app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['PySide6.QtMultimedia'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'PIL', 'PySide6.QtQml',
              'PySide6.QtQuick', 'PySide6.QtWebEngineCore',
              'PySide6.Qt3DCore'],
    noarchive=False,
)

archive = PYZ(analysis.pure)

executable = EXE(
    archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name='Neptune',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
    uac_admin=True,
)
