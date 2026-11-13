# -*- mode: python ; coding: utf-8 -*-
# Build with:  pyinstaller ci-cli.spec
# Output:      dist/ci-cli  (a single self-contained executable)

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

datas, binaries, hiddenimports = [], [], []

# Textual loads its widgets lazily (textual.widgets.__getattr__) and ships
# CSS/data files; Rich and Pygments import lexers, styles and unicode tables
# dynamically. PyInstaller's static analysis misses these, so pull them in.
for pkg in ("textual", "rich", "pygments"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("ci_cli")

# keyring finds its OS backends (macOS Keychain, Secret Service, Windows
# Credential Manager) through package entry points, then imports them by name.
# Bundle keyring's metadata so the entry points exist in the frozen app, and its
# backend modules so they can be imported. Without this the frozen binary falls
# back to keyring's "fail" backend and can't store or read the API key.
d, b, h = collect_all("keyring")
datas += d + copy_metadata("keyring")
binaries += b
hiddenimports += h + collect_submodules("keyring.backends")

a = Analysis(
    ["packaging/launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
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
    name="ci-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
