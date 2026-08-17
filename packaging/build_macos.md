# Building the macOS .app

Run these steps **on a Mac** (PyInstaller cannot cross-compile a macOS
app from Linux/Windows).

## 1. Set up a build environment

```bash
python3 -m venv build-venv
source build-venv/bin/activate
pip install -r requirements.txt -r requirements-macos.txt
pip install pyinstaller
```

## 2. Build

```bash
cd desktop-agent
pyinstaller packaging/agent.spec
```

Output: `dist/HRActivityAgent.app` -- a real, double-clickable app
bundle. `LSUIElement: True` in the spec means it never shows a Dock icon
or app-switcher entry; the menu-bar tray icon is the only visible trace.

## 3. Auto-run at login

```bash
osascript -e 'tell application "System Events" to make login item at end with properties {path:"/Applications/HRActivityAgent.app", hidden:false}'
```

(`hidden:false` is fine here -- `LSUIElement` already means there's
nothing to hide; it never opens a window or Dock icon regardless.)

## 4. Code signing & notarization (required for wide rollout)

An unsigned/unnotarized .app will be blocked by Gatekeeper on other
people's Macs. With an Apple Developer account:

```bash
codesign --deep --force --options runtime --sign "Developer ID Application: Your Company" dist/HRActivityAgent.app
xcrun notarytool submit dist/HRActivityAgent.app.zip --apple-id you@company.com --team-id TEAMID --wait
xcrun stapler staple dist/HRActivityAgent.app
```

## 5. The permission prompt employees will see

The very first time the agent tries to read a window title from another
app, macOS will prompt for **Screen Recording** permission (System
Settings -> Privacy & Security -> Screen Recording). This is expected --
see `collector_macos.py`'s own comment. If the employee denies it, the
agent keeps working exactly as before, just with `windowTitle` always
`None` for that machine; foreground application detection itself does
not need this permission at all.

Consider pre-approving this via an MDM profile (Jamf/Kandji/etc.) for a
smoother rollout rather than relying on each employee to grant it manually.
