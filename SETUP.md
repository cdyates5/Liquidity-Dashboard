# Setup guide — automated dashboard on GitHub

Start to finish, roughly 20 minutes. No prior GitHub Actions experience assumed.

---

## Step 0 — Decide public vs private (read this first)

GitHub Pages is free **only on public repositories**. On a private repo, Pages requires
a paid plan (Pro, Team, or Enterprise). This matters because the repo contains your
methodology, weights, and models.

| You want | Do this |
| --- | --- |
| A private repo, and you have Pro/Team/Enterprise | Private repo + Pages. Follow every step below. |
| A private repo on the free plan | Private repo, **no Pages**. Follow all steps but skip Step 4, and see *Appendix A* — the dashboard is downloaded from the Actions run instead of a URL. |
| Don't mind it being public | Public repo + Pages. Simplest path. Note anyone can read `rebuild.py`. |

Anything published to Pages is reachable by anyone with the link, even from a private
repo — the pages themselves are public unless you enable Pages access control
(Enterprise only). Treat the URL as shareable, not secret.

---

## Step 1 — Get a FRED API key

The build needs one credential. Everything else is keyless.

1. Go to <https://fredaccount.stlouisfed.org/apikeys>.
2. Sign in, or create a free St. Louis Fed account (email + password, no cost).
3. Click **Request API Key**, enter anything reasonable for the description
   (e.g. "internal macro dashboard"), and accept the terms.
4. Copy the 32-character key. Keep the tab open — you need it in Step 3.

---

## Step 2 — Create the repository and add the files

### Option A — browser only (no git installed)

1. Go to <https://github.com/new>.
2. **Repository name**: `global-liquidity-monitor`. Choose **Private** or **Public**
   per Step 0. Tick **Add a README file** (you'll overwrite it). Click
   **Create repository**.
3. On the repo page, click **Add file → Upload files**.
4. Drag in `rebuild.py`, `requirements.txt`, `README.md`, `SETUP.md`, `.gitignore`.
   Click **Commit changes**.
5. The workflow file needs a nested folder, which drag-and-drop can't create.
   Click **Add file → Create new file**. In the filename box type exactly:

   ```
   .github/workflows/build.yml
   ```

   GitHub turns each `/` into a folder as you type. Paste the entire contents of
   `build.yml` into the editor. Click **Commit changes**.

> If `.gitignore` doesn't appear after uploading, that's your OS hiding dotfiles.
> Use **Create new file**, name it `.gitignore`, and paste the contents.

### Option B — command line

```bash
mkdir global-liquidity-monitor && cd global-liquidity-monitor
# copy the five files in, preserving .github/workflows/build.yml
git init -b main
git add .
git commit -m "Global liquidity monitor: build pipeline and workflow"
gh repo create global-liquidity-monitor --private --source=. --push
# no gh CLI? create the repo on github.com first, then:
#   git remote add origin https://github.com/<user>/global-liquidity-monitor.git
#   git push -u origin main
```

Confirm the final layout looks like this:

```
global-liquidity-monitor/
├── .github/
│   └── workflows/
│       └── build.yml
├── .gitignore
├── README.md
├── SETUP.md
├── rebuild.py
└── requirements.txt
```

`build.yml` **must** be at `.github/workflows/build.yml`. Anywhere else and GitHub
silently ignores it — no error, the workflow just never appears.

---

## Step 3 — Add the FRED key as a repository secret

1. In the repo: **Settings** (top bar) → **Secrets and variables** (left sidebar) →
   **Actions**.
2. Click **New repository secret**.
3. **Name**: `FRED_API_KEY` — exactly that, case-sensitive.
4. **Secret**: paste the key from Step 1. No quotes, no spaces.
5. **Add secret**.

You will never see the value again, which is fine; you can overwrite it anytime.
The build fails immediately with a clear message if this is missing or wrong.

Never put the key in `rebuild.py` or any committed file. GitHub scans public repos
and the Fed may revoke a leaked key.

---

## Step 4 — Turn on GitHub Pages

1. **Settings** → **Pages** (left sidebar).
2. Under **Build and deployment** → **Source**, select **GitHub Actions**.

This is the step people get wrong. The default is "Deploy from a branch" — that
publishes files committed to the repo, and this workflow doesn't commit the built
HTML, it uploads it as an artifact. If Source is left on branch mode you'll get a
404 forever.

There's nothing to save; the dropdown applies immediately. No other field needs
touching.

---

## Step 5 — Run the first build

1. Click the **Actions** tab.
2. If you see "Workflows aren't being run on this forked repository" or a prompt to
   enable Actions, click the green **I understand my workflows, go ahead and enable
   them**.
3. In the left sidebar click **Build and publish dashboard**.
4. On the right, click **Run workflow** → keep branch `main` → **Run workflow**.
5. Refresh after a few seconds. A run appears with a yellow dot. Click into it to
   watch. Expect **3–6 minutes**: most of it is the data fetch, since the pipeline
   pulls ~20 years of weekly and monthly series from six providers.

Two jobs run in sequence: **build** (fetch, compute, write `site/index.html`, verify
it) then **deploy** (publish to Pages).

When both show green ticks, open the **deploy** job — the URL is printed in the
"Deploy to GitHub Pages" step, and also under **Settings → Pages**. It looks like:

```
https://<your-username>.github.io/global-liquidity-monitor/
```

The first deployment can take a further 1–2 minutes to become reachable, and the
very first one occasionally 404s for a minute while DNS propagates. Wait, then
hard-refresh (Cmd/Ctrl + Shift + R).

---

## Step 6 — Confirm it's genuinely working

Open the URL and check:

- The header stamp shows a recent build time and `v7 self-contained`.
- The **Week of** date matches the most recent Fed H.4.1 Wednesday.
- Click through all eight tabs — every chart draws.
- Switch **Range** to 1Y and back to Inception.
- Click **Weekly CSV**; a file downloads.

If charts are missing, an orange bar at the bottom of the page names the error.

---

## Step 7 — Let it run itself

Nothing further is required. The schedule in `build.yml` is:

```yaml
- cron: "0 2 * * 5"
```

That's **02:00 UTC every Friday** = midday Melbourne. The Fed releases H.4.1 on
Thursday at 16:30 ET (~20:30 UTC), so each Friday run picks up that week's fresh
Wednesday print with hours to spare.

To change cadence, edit that line and commit (<https://crontab.guru> is useful):

| Want | Cron |
| --- | --- |
| Twice weekly (Mon + Fri) | `0 2 * * 1,5` |
| Every weekday | `0 2 * * 1-5` |
| Daily | `0 2 * * *` |

Two GitHub behaviours worth knowing. Scheduled runs on shared runners can start
5–45 minutes late at busy times — irrelevant for weekly data. And GitHub disables
cron on repos with **no commits for 60 days**, emailing you first; a manual **Run
workflow** or any commit re-arms it.

---

## Troubleshooting

**Workflow doesn't appear under Actions.** `build.yml` isn't at
`.github/workflows/build.yml`, or Actions is disabled (Settings → Actions → General →
Allow all actions).

**Build fails: `FRED_API_KEY is not set`.** The secret is missing, misnamed, or was
added to an Environment instead of **Repository secrets**. Re-do Step 3.

**Build fails inside "Build dashboard".** Open the step and read the traceback. An
upstream source being briefly down is the usual cause — the fetchers already retry
with backoff, so just click **Re-run all jobs**. If one provider changed its API,
the error names the failing fetch function.

**Build succeeds, deploy fails with a permissions error.** Settings → Actions →
General → **Workflow permissions** → ensure **Read and write permissions**, or
confirm the `permissions:` block at the top of `build.yml` is intact.

**Site 404s.** Almost always Step 4 — Source must be **GitHub Actions**, not a
branch. Also confirm the deploy job actually succeeded.

**Site loads but shows old data.** Browser cache. Hard-refresh. Check the header's
build timestamp to confirm which build you're looking at.

**Everything green but you want to be sure it's fresh.** The header stamp and the
Methodology tab's source table both carry the as-of dates for every series.

---

## Appendix A — private repo without Pages (free plan)

Skip Step 4. The workflow still builds and still verifies; the dashboard is attached
to each run instead of published.

Add this step to the end of the `build` job in `build.yml`, and delete the `deploy`
job and the `permissions`/`concurrency` blocks:

```yaml
      - name: Attach dashboard to this run
        uses: actions/upload-artifact@v7
        with:
          name: dashboard
          path: site/index.html
          retention-days: 90
```

To get the dashboard: **Actions** → newest run → scroll to **Artifacts** → download
`dashboard` → unzip → open `index.html`. It's a single self-contained file, so it
works from disk and can be emailed or dropped in shared storage.

---

## Appendix B — running it locally

```bash
pip install -r requirements.txt
export FRED_API_KEY=your_key_here          # Windows PowerShell: $env:FRED_API_KEY="..."
python3 rebuild.py --out site --name index.html
open site/index.html                        # Linux: xdg-open, Windows: start
```

Omit both flags to write `global_liquidity_monitor.html` to the default output
directory instead.

---

## Appendix C — what the workflow actually does

1. **checkout** — clones the repo onto a fresh Ubuntu runner.
2. **setup-python** — installs Python 3.12, caching pip between runs.
3. **pip install** — pandas and numpy.
4. **Build dashboard** — runs `rebuild.py`, which fetches every series, recomputes
   the GLI proxy, global M2 aggregate and lead index, vendors Chart.js inline, and
   writes `site/index.html`. Then three assertions run: the file is over 300 KB, the
   data payload was injected, and Chart.js was embedded. **Any failure here aborts
   before deployment, so the live site keeps the last good build.**
5. **upload-pages-artifact** — packages `site/`.
6. **deploy-pages** — publishes it.

The actions are pinned to majors that run on Node 24 (`checkout@v6`,
`setup-python@v6`, `upload-pages-artifact@v5`, `deploy-pages@v5`), since GitHub
removes Node 20 from runners on 16 September 2026. If you copy workflow snippets
from older blog posts, check their versions against these.
