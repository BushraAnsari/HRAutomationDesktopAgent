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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="HRActivityAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # <-- no console window, Windows
    windowed=True,       # <-- no console window, macOS/Linux
    icon=None,           # set to "agent.ico" (Windows) once a real icon exists
)

# macOS only: wraps the executable into a proper double-clickable .app
# with LSUIElement so it never shows a Dock icon either (menu bar tray
# icon only, matching "no interface" on macOS too).
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="HRActivityAgent.app",
        icon=None,  # set to "agent.icns" once a real icon exists
        bundle_identifier="com.yourcompany.hractivityagent",
        info_plist={
            "LSUIElement": True,  # no Dock icon, menu-bar-only app
            "NSHighResolutionCapable": True,
        },
    )
