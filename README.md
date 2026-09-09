# Global Liquidity Monitor

Self-contained macro dashboard: a weekly CrossBorder Capital–style Global Liquidity
Index proxy, the four-economy global M2 aggregate, and a lead-indicator composite —
rebuilt from primary sources and published to GitHub Pages every Friday.

`rebuild.py` fetches every series, recomputes the models, and writes one standalone
HTML file. Chart.js is vendored inline, so the page renders with no network access.

## Setup (once)

**Full step-by-step walkthrough, including troubleshooting: [SETUP.md](SETUP.md).**
The short version:

1. **Create the repo** and push these files (`rebuild.py`, `requirements.txt`,
   `.github/workflows/build.yml`, `.gitignore`, this README).

2. **Add the FRED key.** Settings → Secrets and variables → Actions → New repository
   secret. Name it `FRED_API_KEY`. Free key: <https://fredaccount.stlouisfed.org/apikeys>.
   The build fails fast without it.

3. **Turn on Pages.** Settings → Pages → Build and deployment → Source: **GitHub Actions**.
   (Not "Deploy from a branch" — this workflow uploads an artifact.)

4. **Run it.** Actions → *Build and publish dashboard* → Run workflow. First run takes
   ~3–5 minutes. The URL appears in the deploy step and under Settings → Pages,
   typically `https://<user>.github.io/<repo>/`.

## Schedule

Cron is `0 2 * * 5` — 02:00 UTC Friday, i.e. midday Melbourne. The Fed's H.4.1 releases
Thursday 16:30 ET (~20:30 UTC), so each run picks up that week's fresh Wednesday print.
Adjust the cron in `build.yml` to change cadence; `workflow_dispatch` lets you rebuild
on demand at any time.

Note that GitHub may skip scheduled runs on repos with no activity for ~60 days, and
cron on shared runners can drift by tens of minutes. Neither matters for weekly data.

## Failure behaviour

If any upstream source is down or a sanity check trips, the build job fails and
**nothing is deployed** — the previously published dashboard stays live. You get an
email from GitHub with the failing log. Re-run from the Actions tab once the source
recovers; the fetchers already retry transient errors with backoff.

## Local use

```bash
export FRED_API_KEY=...          # or put it in .env and source it
pip install -r requirements.txt
python3 rebuild.py --out site --name index.html
open site/index.html
```

`--out` and `--name` default to the original single-file output path when omitted.

## Data sources

All keyless except FRED: DBnomics (Fed H.4.1, H.8, OECD BCI), ECB SDMX (ILM, BSI, EXR),
BoJ official API (BS01, MD02), East Money (China M2), Yahoo Finance (DXY), FRED
(US M2, DGS2, DGS10, WTI, China M2 pre-2008 splice). Methodology, substitutions and
known divergences from the published CrossBorder index are documented in the
dashboard's own Methodology tab.

Research reconstruction for internal use. Not investment advice.
