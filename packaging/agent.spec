# PyInstaller spec -- builds a single windowless executable on whichever
# OS this is run on (Windows -> .exe, macOS -> .app bundle via BUNDLE
# below). Run with: pyinstaller packaging/agent.spec
#
# --windowed / console=False is what actually satisfies "run quietly in
# the background" -- without it, a plain PyInstaller build on Windows
# pops a visible console window on every launch.
import sys

block_cipher = None

a = Analysis(
    ["../agent/main.py"],
    pathex=["../"],
    binaries=[],
    datas=[],
    hiddenimports=[
        "pystray._win32" if sys.platform == "win32" else
        "pystray._darwin" if sys.platform == "darwin" else
        "pystray._xorg",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    # exclude_binaries=True is what actually makes this "onedir" mode --
    # binaries/zipfiles/datas are collected separately below (COLLECT),
    # not bundled straight into the .exe itself (that's --onefile mode
    # instead, and is exactly the mismatch that broke the very first CI
    # build here: the workflow's own upload step expects a
    # dist/HRActivityAgent/ *folder*, which only exists in onedir mode --
    # a onefile build produces a single dist/HRActivityAgent.exe file
    # with no such folder, so "no files found" was the correct, if
    # confusing, thing for the upload step to say).
    exclude_binaries=True,
    name="HRActivityAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,       # <-- no console window, Windows
    windowed=True,       # <-- no console window, macOS/Linux
    icon=None,           # set to "agent.ico" (Windows) once a real icon exists
)

# The actual onedir step -- everything exe.py excluded above gets
# collected here instead, producing dist/HRActivityAgent/ as a real
# folder containing HRActivityAgent.exe plus its dependencies. This is
# what build-agent.yml's own upload-artifact step (path: dist/HRActivityAgent/)
# has always expected to exist.
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="HRActivityAgent",
)

# macOS only: wraps the onedir COLLECT output (not the bare exe) into a
# proper double-clickable .app, with LSUIElement so it never shows a Dock
# icon either (menu bar tray icon only, matching "no interface" on macOS
# too). Bundling from coll rather than exe directly is the standard,
# recommended PyInstaller pattern for a macOS .app -- it was working
# before purely because BUNDLE() tolerates a onefile EXE as input too,
# not because that was the intended, correct setup.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="HRActivityAgent.app",
        icon=None,  # set to "agent.icns" once a real icon exists
        bundle_identifier="com.yourcompany.hractivityagent",
        info_plist={
            "LSUIElement": True,  # no Dock icon, menu-bar-only app
            "NSHighResolutionCapable": True,
        },
    )
