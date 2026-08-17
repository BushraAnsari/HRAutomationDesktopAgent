# Getting a real .exe and .app -- no terminal, no commands

This is the "someone else's Windows machine and someone else's Mac build
it for you" path. Every step below is clicking in a web browser.

## 1. Put this project on GitHub

- Go to github.com, click **New repository**, give it any name (e.g.
  `hr-activity-agent`), create it.
- On the new repo's page, click **uploading an existing file** (or drag
  the whole `desktop-agent` folder's contents into the browser) and
  commit them. That's it -- no `git` command needed for this.

## 2. Let it build automatically

The moment those files land on the `main` branch, GitHub notices the
`.github/workflows/build-agent.yml` file included in this project and
starts two builds automatically -- one on a real Windows machine, one on
a real Mac, both owned and run by GitHub, not you.

Click the **Actions** tab at the top of your repo. You'll see a run
called "Build Desktop Agent" in progress (a small yellow dot), then
green checkmarks once both finish (usually 3-5 minutes).

## 3. Download the finished files

Click into that completed run. Near the bottom of the page is an
**Artifacts** section with two entries:

- **HRActivityAgent-Windows** -- click to download a zip containing the
  actual `HRActivityAgent.exe` (and its supporting files)
- **HRActivityAgent-macOS** -- click to download a zip containing
  `HRActivityAgent.app`

Unzip either one and you have the real, runnable file for that OS --
built on the real OS it needs to run on, with nothing installed on your
own computer.

## 4. Whenever you change the agent's code

Just upload the changed file(s) again the same way (or connect a proper
git client later if you want something less manual) -- every push
re-triggers both builds and produces fresh files.

## If you'd rather trigger it manually instead of on every push

In the **Actions** tab, click "Build Desktop Agent" on the left, then the
**Run workflow** button on the right, then **Run workflow** again in the
dropdown. Same result, just on-demand instead of automatic.

## What you still need to do yourself (can't be automated by CI)

- **Point it at your real backend**: edit `agent/config.py`'s
  `DEFAULTS["api_base_url"]` to your actual server address *before*
  uploading, so the built .exe/.app already knows where to connect
  (rather than everyone's agent defaulting to `localhost`).
- **Auto-run at login / installer wrapping / code signing**: these are
  one-time setup steps on the actual distribution, covered in
  `packaging/build_windows.md` (step 3+) and `packaging/build_macos.md`
  (step 3+) -- CI builds the app itself, but signing/notarizing needs
  your own Apple/Windows developer certificates, which can't live in a
  public build log for security reasons.
