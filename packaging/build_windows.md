# Building the Windows .exe

Run these steps **on a Windows machine** (PyInstaller builds for the OS
it's run on -- this cannot be cross-compiled from Linux/macOS).

## 1. Set up a build environment

```powershell
py -3.11 -m venv build-venv
build-venv\Scripts\activate
pip install -r requirements.txt -r requirements-windows.txt
pip install pyinstaller
```

## 2. Build

```powershell
cd desktop-agent
pyinstaller packaging\agent.spec
```

Output: `dist\HRActivityAgent\HRActivityAgent.exe` (a folder distribution,
not `--onefile` -- see the spec's own comment on why that's the better
default here).

## 3. Auto-run at login -- now automatic, nothing to do here

The built .exe registers itself for auto-start the first time it ever
runs (see agent/autostart.py) -- it writes a per-user Windows registry
Run key pointing at its own .exe path, the same mechanism most consumer
background apps already use for this, needing no admin rights and no
separate schtasks command. An employee's entire setup is: run the exe
once, enter the pairing code, done -- it starts on its own at every
login after that.

If you'd rather manage this centrally instead (e.g. via Group Policy or
an MDM-pushed Scheduled Task, so IT controls it rather than the app
self-registering), the old manual approach still works and simply
duplicates what the app already does on its own:

```powershell
schtasks /create /tn "HR Activity Agent" /tr "C:\Path\To\HRActivityAgent.exe" ^
  /sc onlogon /rl limited /f
```

## 4. Code signing (recommended before wide rollout)

An unsigned .exe will trigger a Windows SmartScreen warning on first run.
Sign `HRActivityAgent.exe` with your organization's code-signing
certificate:

```powershell
signtool sign /f YourCert.pfx /p <password> /t http://timestamp.digicert.com HRActivityAgent.exe
```

## 5. Configuring which server it talks to

Before distributing, either:
- Ship a `config.json` alongside the installer pre-set with your org's
  `api_base_url` (dropped into the app-data folder `agent/config.py`
  resolves -- see that file's own `get_app_data_dir()`), or
- Set it via an environment variable / installer parameter and have
  `config.py` read that on first run (a small addition if you want this
  instead of a static file).
