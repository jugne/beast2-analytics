# beast2-analytics

This repository hosts a webpage that displays:
1. Download statistics for BEAST2 and its packages.
2. Package dependency network.

The site is rebuilt by a GitHub Actions workflow (`.github/workflows/stats.yml`)
- on the 1st of each month, 
- on every push to `main` that touches a script, 
- on demand via the "Run workflow" button. 

The workflow takes a new download snapshot, regenerates both pages into `docs/`,
 commits the results and deploys `docs/` to GitHub Pages.

## Repository layout

| Path | Purpose |
|------|---------|
| `beast2_release_stats.py` | Collects GitHub release download counts and builds the statistics page |
| `beast2_dependency_graph.py` | Builds the interactive dependency network page |
| `beast2_site.py` | Shared header and tab navigation used by both pages |
| `beast2_snapshots/` | One JSON snapshot of cumulative download counts per run; the data source for the statistics |
| `docs/` | Generated site: `index.html`, `dependencies.html`, `beast2_download_stats.csv` |

## Running locally

Requirements: Python 3.10 or newer and `git`. Both scripts use only the
standard library, so there is nothing to install.

```bash
git clone https://github.com/<you>/beast2-analytics.git
cd beast2-analytics
```

### 1. Rebuild the pages from the existing snapshots

This makes no GitHub API calls and is the quickest way to check changes to the
HTML or the scripts:

```bash
python3 beast2_release_stats.py --report \
    --output-html docs/index.html \
    --output-csv docs/beast2_download_stats.csv
python3 beast2_dependency_graph.py --output docs/dependencies.html
```

Open `docs/index.html` in a browser. The tab links between the two pages are
relative, so both files must live in the same folder.

### 2. Take a new download snapshot

Set a GitHub token first. Without one the API allows only 60 requests per hour,
which is not enough for all packages.

```bash
export GITHUB_TOKEN=ghp_...        # any token with public repo read access
python3 beast2_release_stats.py
```

The script writes `beast2_snapshots/snapshot_<date>.json` and then builds the
report. It refuses to take a second snapshot in the same calendar month unless
you pass `--force`.

### 3. Reproduce the full workflow build

```bash
export GITHUB_TOKEN=ghp_...
python3 beast2_release_stats.py --output-html /dev/null --output-csv /dev/null
python3 beast2_release_stats.py --report --backfill-dates --cban-dir .cban \
    --output-html docs/index.html \
    --output-csv docs/beast2_download_stats.csv
python3 beast2_dependency_graph.py --output docs/dependencies.html
```

The `.cban` directory is a local clone of the CBAN registry and is ignored by
git.

## Script options

### `beast2_release_stats.py`

Collects download counts for every `.zip` release asset of every package listed
in the CBAN registry, stores them as a snapshot, and computes per-period
downloads from the differences between snapshots.

By default the script takes a new snapshot and then builds the report. With
`--report` it only builds the report.

| Option | Default | Description |
|--------|---------|-------------|
| `--report` | off | Skip the snapshot and build the report from existing snapshots only. No GitHub API calls are made unless `--backfill-dates` is also given. |
| `--force` | off | Take a snapshot even if one already exists for the current month. |
| `--snapshots-dir DIR` | `beast2_snapshots` | Where snapshots are read from and written to. |
| `--output-html FILE` | `beast2_download_stats.html` | Path of the generated statistics page. Use `/dev/null` to skip it. |
| `--output-csv FILE` | `beast2_download_stats.csv` | Path of the generated CSV with the same per-package, per-period numbers as the page. Use `/dev/null` to skip it. |
| `--cban-dir DIR` | unset | Path to a local clone of `CompEvol/CBAN`. The clone is created if missing and pulled otherwise. Its git history is used to find when each package was first added to CBAN, which the page shows as a "from …" badge for packages added after tracking began. Omit to skip this step. |
| `--backfill-dates` | off | Fetch release publication dates that are missing from the latest snapshot. Needed once after adding the date feature or after a snapshot taken by an older version of the script. Requires `GITHUB_TOKEN` and is slow without it. |

Environment:

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | GitHub API token. Optional, but strongly recommended for taking snapshots and required in practice for `--backfill-dates`. Raises the rate limit from 60 to 5000 requests per hour. |

Behaviour notes:

- With only one snapshot the page shows cumulative totals. From two snapshots
  onwards it shows downloads per year computed from snapshot deltas.
- The first snapshot that includes a package becomes that package's baseline.
  Those downloads count towards the all-time total but not the tracked total.

### `beast2_dependency_graph.py`

Reads the `<depends>` entries of the latest listed version of every package in
the CBAN registry and writes an interactive force-directed network.

| Option | Default | Description |
|--------|---------|-------------|
| `--xml FILE_OR_URL [...]` | the two CBAN 2.7 registry URLs | One or more CBAN package XML files or URLs to read. Pass local files to build the network for a modified or older registry. |
| `--snapshots-dir DIR` | `beast2_snapshots` | Directory of download snapshots. If present, the latest snapshot is used to offer "All-time downloads" as a node-size option on the page. If missing or empty, that option is simply unavailable. |
| `--output FILE` | `beast2_dependencies.html` | Path of the generated page. |

### `beast2_site.py`

Not a command-line tool. It defines the site title, the list of pages and the
header styles that both generators insert into their output. Edit `PAGES` there
to add a page to the navigation.
