#!/usr/bin/env python3
"""
BEAST2 Package Release Download Statistics Scraper

Collects GitHub release download statistics for all BEAST2 packages
listed in the CBAN package registry. Uses a snapshot-based approach:
each run saves cumulative download counts, and deltas between snapshots
give you actual downloads per time period.

Usage:
    export GITHUB_TOKEN=your_token_here
    python beast2_release_stats.py              # Take a new snapshot
    python beast2_release_stats.py --report     # Generate report from existing snapshots
"""

import os
import sys
import time
import json
import csv
import argparse
from collections import defaultdict
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from xml.etree import ElementTree
from urllib.parse import urlparse


CBAN_URLS = [
    "https://raw.githubusercontent.com/CompEvol/CBAN/master/packages2.7.xml",
    "https://raw.githubusercontent.com/CompEvol/CBAN/master/packages-extra-2.7.xml",
]

GITHUB_API = "https://api.github.com"
SNAPSHOTS_DIR = "beast2_snapshots"


def get_headers(token):
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def api_get(url, token, _retries=0):
    """Make a GitHub API GET request with rate limit handling."""
    headers = get_headers(token)
    req = Request(url, headers=headers)
    try:
        with urlopen(req) as resp:
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if remaining and int(remaining) < 10:
                reset_time = int(resp.headers.get("X-RateLimit-Reset", 0))
                wait = max(0, reset_time - int(time.time())) + 1
                print(f"  Rate limit nearly exhausted. Waiting {wait}s...")
                time.sleep(wait)
            data = json.loads(resp.read().decode())
            # Get Link header for pagination
            link_header = resp.headers.get("Link", "")
            next_url = None
            if link_header:
                for part in link_header.split(","):
                    if 'rel="next"' in part:
                        next_url = part.split("<")[1].split(">")[0]
            return data, next_url
    except HTTPError as e:
        if e.code == 404:
            return None, None
        if e.code in (403, 429) and _retries < 3 and (
                e.headers.get("X-RateLimit-Remaining") == "0" or e.headers.get("Retry-After")):
            reset_time = int(e.headers.get("X-RateLimit-Reset", 0))
            wait = int(e.headers.get("Retry-After") or max(0, reset_time - int(time.time()))) + 2
            print(f"  Rate limited. Waiting {wait}s...")
            time.sleep(wait)
            return api_get(url, token, _retries + 1)
        print(f"  HTTP {e.code} for {url}")
        return None, None
    except URLError as e:
        print(f"  URL error for {url}: {e}")
        return None, None


def fetch_xml(url):
    """Fetch and parse an XML file from a URL."""
    print(f"Fetching {url}")
    req = Request(url)
    with urlopen(req) as resp:
        return resp.read().decode()


def extract_repos_from_xml(xml_content):
    """Extract unique GitHub owner/repo pairs from package XML."""
    repos = {}  # repo_key -> package_name
    root = ElementTree.fromstring(xml_content)

    for pkg in root.findall("package"):
        name = pkg.get("name", "unknown")
        url = pkg.get("url", "")
        project_url = pkg.get("projectURL", "")

        repo_key = _extract_github_repo(url) or _extract_github_repo(project_url)
        if repo_key and repo_key not in repos:
            repos[repo_key] = name

    return repos


def _extract_github_repo(url):
    """Extract 'owner/repo' from a GitHub URL."""
    if not url:
        return None
    parsed = urlparse(url)
    if "github.com" not in (parsed.hostname or ""):
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return None


def get_all_releases(owner_repo, token):
    """Get all releases for a repository (paginated)."""
    releases = []
    url = f"{GITHUB_API}/repos/{owner_repo}/releases?per_page=100"
    while url:
        data, next_url = api_get(url, token)
        if data is None:
            break
        releases.extend(data)
        url = next_url
    return releases


def collect_snapshot(repos, token):
    """Collect current cumulative download counts for all repos.

    Returns a dict: {package_name: {release_tag: {asset_name: download_count}}}
    """
    snapshot = {}
    total = len(repos)

    for i, (repo_key, pkg_name) in enumerate(repos.items(), 1):
        print(f"[{i}/{total}] {pkg_name} ({repo_key})")
        releases = get_all_releases(repo_key, token)
        if not releases:
            print(f"  No releases found")
            continue

        pkg_data = {}
        published = {}
        for release in releases:
            tag = release.get("tag_name", "unknown")
            assets = release.get("assets", [])
            asset_counts = {}
            for asset in assets:
                asset_name = asset.get("name", "")
                if asset_name.endswith(".zip"):
                    asset_counts[asset_name] = asset.get("download_count", 0)
            if asset_counts:
                pkg_data[tag] = asset_counts
                if release.get("published_at"):
                    published[tag] = release["published_at"][:10]

        if pkg_data:
            snapshot[pkg_name] = {"repo": repo_key, "releases": pkg_data,
                                  "published": published}

        time.sleep(0.1)

    return snapshot


def save_snapshot(snapshot, snapshots_dir):
    """Save a snapshot to disk with a timestamp filename."""
    os.makedirs(snapshots_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = os.path.join(snapshots_dir, f"snapshot_{timestamp}.json")
    with open(filename, "w") as f:
        json.dump({"timestamp": timestamp, "data": snapshot}, f, indent=2)
    print(f"Snapshot saved to {filename}")
    return filename


def load_snapshots(snapshots_dir):
    """Load all snapshots from disk, sorted by timestamp."""
    if not os.path.isdir(snapshots_dir):
        return []
    snapshots = []
    for fname in sorted(os.listdir(snapshots_dir)):
        if fname.startswith("snapshot_") and fname.endswith(".json"):
            filepath = os.path.join(snapshots_dir, fname)
            with open(filepath) as f:
                snapshots.append(json.load(f))
    return snapshots


def backfill_release_dates(snapshots_dir, token):
    """Add release dates to the latest snapshot for packages that don't have them yet.

    Snapshots taken before release dates were recorded lack them. Release dates
    never change, so this only needs the API once per package; afterwards it
    makes no calls at all.
    """
    files = sorted(f for f in os.listdir(snapshots_dir)
                   if f.startswith("snapshot_") and f.endswith(".json")) if os.path.isdir(snapshots_dir) else []
    if not files:
        return
    path = os.path.join(snapshots_dir, files[-1])
    with open(path) as f:
        snap = json.load(f)

    missing = [pkg for pkg, info in snap["data"].items()
               if set(info["releases"]) - set(info.get("published", {}))]
    if not missing:
        return
    print(f"Backfilling release dates for {len(missing)} package(s) in {files[-1]}")
    for pkg in missing:
        info = snap["data"][pkg]
        published = info.setdefault("published", {})
        for release in get_all_releases(info["repo"], token):
            tag = release.get("tag_name")
            if tag in info["releases"] and release.get("published_at"):
                published[tag] = release["published_at"][:10]
        time.sleep(0.1)
    with open(path, "w") as f:
        json.dump(snap, f, indent=2)


def tracking_starts(snapshots):
    """Return {package: timestamp of the first snapshot that contains it}."""
    first = {}
    for snap in snapshots:
        for pkg in snap["data"]:
            first.setdefault(pkg, snap["timestamp"])
    return first


def compute_deltas(snapshots):
    """Compute download deltas between consecutive snapshots.

    Returns a list of dicts:
        [{package, repo, release_tag, period_start, period_end, period_year, zip_downloads_delta}, ...]

    The first snapshot that contains a package is only used as its baseline:
    its cumulative counts go into the all-time total but not into any period,
    because we can't know when those downloads happened. From then on, each
    package is compared with the last snapshot in which it appeared, so a
    package that is temporarily missing (e.g. an API error) doesn't have its
    whole history counted again when it comes back.

    A release that appears for an already-tracked package starts from 0,
    which is correct: all its downloads happened since the previous snapshot.
    """
    if len(snapshots) < 2:
        return []

    deltas = []
    last_seen = {}  # package -> (timestamp, releases) from its latest snapshot
    for snap in snapshots:
        curr_ts = snap["timestamp"]
        period_year = curr_ts[:4]

        for pkg_name, pkg_info in snap["data"].items():
            if pkg_name in last_seen:
                prev_ts, prev_releases = last_seen[pkg_name]
                for tag, assets in pkg_info["releases"].items():
                    for asset_name, curr_count in assets.items():
                        prev_count = prev_releases.get(tag, {}).get(asset_name, 0)
                        delta = curr_count - prev_count
                        if delta > 0:
                            deltas.append({
                                "package": pkg_name,
                                "repo": pkg_info["repo"],
                                "release_tag": tag,
                                "period_start": prev_ts,
                                "period_end": curr_ts,
                                "period_year": period_year,
                                "zip_downloads_delta": delta,
                            })
            last_seen[pkg_name] = (curr_ts, pkg_info["releases"])

    return deltas


def compute_cumulative_by_year(snapshots):
    """If only one snapshot exists, show cumulative totals (no delta possible).

    Returns stats grouped by release year (based on the data available).
    """
    if not snapshots:
        return []
    latest = snapshots[-1]["data"]
    stats = []
    for pkg_name, pkg_info in latest.items():
        repo = pkg_info["repo"]
        for tag, assets in pkg_info["releases"].items():
            total_zip = sum(assets.values())
            if total_zip > 0:
                stats.append({
                    "package": pkg_name,
                    "repo": repo,
                    "release_tag": tag,
                    "period_year": "cumulative",
                    "zip_downloads_delta": total_zip,
                })
    return stats

def snapshot_exists_this_month(snapshots_dir):
    """Return True if a snapshot for the current month already exists."""
    if not os.path.isdir(snapshots_dir):
        return False
    prefix = f"snapshot_{datetime.now().strftime('%Y-%m')}"
    return any(f.startswith(prefix) and f.endswith(".json")
               for f in os.listdir(snapshots_dir))


def write_csv(stats, filename, is_delta=True):
    """Write stats to CSV file."""
    if is_delta:
        fieldnames = ["package", "repo", "release_tag", "period_start", "period_end", "period_year", "zip_downloads_delta"]
    else:
        fieldnames = ["package", "repo", "release_tag", "period_year", "zip_downloads_delta"]

    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(stats)
    print(f"CSV written to {filename}")


def _version_key(tag):
    """Natural sort key for release tags, e.g. v1.10.0 > v1.9.2."""
    import re
    parts = re.findall(r"\d+|[A-Za-z]+", tag)
    return [(0, int(p)) if p.isdigit() else (1, p.lower()) for p in parts]


def cban_added_dates(cban_dir):
    """Return {package: 'YYYY-MM-DD'} of the first CBAN commit that listed it.

    Clones CompEvol/CBAN into cban_dir on first use (a few MB), pulls afterwards,
    then walks the history of all package XML files once.
    """
    import re
    import subprocess
    try:
        if os.path.isdir(os.path.join(cban_dir, ".git")):
            subprocess.run(["git", "-C", cban_dir, "pull", "-q"], check=True)
        else:
            subprocess.run(["git", "clone", "-q", "https://github.com/CompEvol/CBAN.git", cban_dir],
                           check=True)
        log = subprocess.run(
            ["git", "-C", cban_dir, "log", "--reverse", "--format=@@%as", "-p", "-U0", "--", "*.xml"],
            check=True, capture_output=True, text=True, errors="replace").stdout
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"Could not read CBAN history: {e}")
        return {}

    added, date = {}, None
    pat = re.compile(r"""^\+.*<package\b[^>]*\bname=["']([^"']+)["']""")
    for line in log.splitlines():
        if line.startswith("@@") and not line.startswith("@@ "):
            date = line[2:]
        else:
            m = pat.match(line)
            if m:
                added.setdefault(m.group(1), date)
    print(f"CBAN history: found addition dates for {len(added)} packages")
    return added


def generate_html(stats, filename, snapshots, is_delta=True, cban_added=None):
    """Generate an HTML report with a chart and an expandable per-package table."""
    pkg_year_totals = defaultdict(lambda: defaultdict(int))
    pkg_totals = defaultdict(int)
    pkg_repo = {}
    all_years = set()
    # package -> tag -> period_end -> downloads
    pkg_ver_period = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

    for row in stats:
        pkg = row["package"]
        year = row["period_year"]
        count = row["zip_downloads_delta"]
        pkg_year_totals[pkg][year] += count
        pkg_totals[pkg] += count
        pkg_repo[pkg] = row["repo"]
        all_years.add(year)
        pkg_ver_period[pkg][row["release_tag"]][row.get("period_end", "cumulative")] += count

    years_sorted = sorted(y for y in all_years if y != "cumulative")
    if "cumulative" in all_years:
        years_sorted.append("cumulative")

    # All-time cumulative counts from the latest snapshot
    latest = snapshots[-1]["data"] if snapshots else {}
    cban_added = cban_added or {}

    # Packages seen only once so far (baseline) have no deltas yet but should still be listed
    for pkg in latest:
        pkg_totals[pkg] += 0
    sorted_packages = sorted(pkg_totals.keys(), key=lambda p: pkg_totals[p], reverse=True)

    starts = tracking_starts(snapshots)
    global_start = snapshots[0]["timestamp"] if snapshots else ""
    alltime_ver = {
        pkg: {tag: sum(assets.values()) for tag, assets in info["releases"].items()}
        for pkg, info in latest.items()
    }
    alltime_pkg = {pkg: sum(v.values()) for pkg, v in alltime_ver.items()}

    # Periods between consecutive snapshots
    def _fmt(ts):
        return datetime.strptime(ts[:10], "%Y-%m-%d").strftime("%d %b %Y")

    if is_delta:
        periods = [
            {"end": snapshots[i]["timestamp"],
             "label": f"{_fmt(snapshots[i-1]['timestamp'])} – {_fmt(snapshots[i]['timestamp'])}"}
            for i in range(1, len(snapshots))
        ]
    else:
        periods = [{"end": "cumulative", "label": "Cumulative"}]

    # Release dates: union over all snapshots (they never change)
    all_published = defaultdict(dict)
    for snap in snapshots:
        for pkg, info in snap["data"].items():
            all_published[pkg].update(info.get("published", {}))

    # Per-package detail data for the expandable rows
    details = {}
    for pkg in sorted_packages:
        tags = set(pkg_ver_period[pkg]) | set(alltime_ver.get(pkg, {}))
        versions = []
        start = starts.get(pkg, global_start)
        # None = period ended before/at this package's baseline snapshot (not tracked)
        tracked_mask = [not is_delta or p["end"] > start for p in periods]
        published = all_published.get(pkg, {})
        for tag in sorted(tags, key=_version_key, reverse=True):
            per_period = [pkg_ver_period[pkg][tag].get(p["end"], 0) if ok else None
                          for p, ok in zip(periods, tracked_mask)]
            versions.append({
                "tag": tag,
                "published": _fmt(published[tag]) if tag in published else "",
                "alltime": alltime_ver.get(pkg, {}).get(tag, 0),
                "tracked": sum(x or 0 for x in per_period),
                "periods": per_period,
            })
        details[pkg] = {
            "repo": pkg_repo.get(pkg, latest.get(pkg, {}).get("repo", "")),
            "since": _fmt(start) if start else "",
            "late": bool(start) and start > global_start,
            "cban": _fmt(cban_added[pkg]) if pkg in cban_added else "",
            "periodTotals": [sum(v["periods"][i] or 0 for v in versions) if ok else None
                             for i, ok in enumerate(tracked_mask)],
            "versions": versions,
        }

    # Chart data (top 30)
    top_n = 30
    chart_packages = sorted_packages[:top_n]
    chart_datasets = []
    colors = [
        "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
        "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
        "#5778a4", "#e49444", "#d1615d", "#85b6b2", "#6a9f58",
        "#e7ca60", "#a87c9f", "#f1a2a9", "#967662", "#b8b0a8",
    ]

    for idx, year in enumerate(years_sorted):
        data = [pkg_year_totals[pkg].get(year, 0) for pkg in chart_packages]
        chart_datasets.append({
            "label": str(year),
            "data": data,
            "backgroundColor": colors[idx % len(colors)],
        })

    # Table: one <tbody> per package (summary row + hidden detail row) so
    # sorting and filtering move them together.
    n_cols = 4 + len(years_sorted)
    table_rows = []
    for pkg in sorted_packages:
        start = starts.get(pkg, global_start)

        def _year_cell(y):
            v = f"{pkg_year_totals[pkg].get(y, 0):,}"
            # Partial year: tracking for this package began after 1 January of y
            if is_delta and start[:4] == y and start[5:10] > "01-01":
                return (f"<td class='num partial' title='Partial year: tracked from "
                        f"{_fmt(start)}'>{v}</td>")
            if is_delta and start[:4] > y:
                return "<td class='num na' title='Not tracked in this year'>–</td>"
            return f"<td class='num'>{v}</td>"

        year_cells = "".join(_year_cell(y) for y in years_sorted)
        n_versions = len(details[pkg]["versions"])
        d = details[pkg]
        badge = ""
        if d["late"]:
            if pkg_totals[pkg] == 0 and start == snapshots[-1]["timestamp"]:
                badge = (f"<span class='badge new' title='First seen in the latest snapshot "
                         f"({d['since']}). Downloads will be tracked from the next snapshot.'>new</span>")
            else:
                badge = (f"<span class='badge' title='Tracked from {d['since']}"
                         + (f"; added to CBAN {d['cban']}" if d['cban'] else "")
                         + f"'>from {d['since']}</span>")
        table_rows.append(
            f"<tbody class='pkg' data-pkg=\"{pkg}\">"
            f"<tr class='summary-row' onclick='toggle(this.parentNode)'>"
            f"<td class='pkg-name'><span class='caret'>&#9656;</span>{pkg}{badge}</td>"
            f"<td class='num'>{pkg_totals[pkg]:,}</td>{year_cells}"
            f"<td class='num'>{alltime_pkg.get(pkg, 0):,}</td>"
            f"<td class='num'>{n_versions}</td></tr>"
            f"<tr class='detail-row'><td colspan='{n_cols}'></td></tr>"
            f"</tbody>"
        )

    def _th(label, idx):
        cls = " class='num'" if idx > 0 else ""
        return f"<th{cls} onclick=\"sortTable({idx})\">{label} <span class=\"sort-arrow\">&#9650;&#9660;</span></th>"

    year_headers = "".join(_th(y, i + 2) for i, y in enumerate(years_sorted))
    extra_headers = _th("All-time", 2 + len(years_sorted)) + _th("Versions", 3 + len(years_sorted))

    # Snapshot info
    num_snapshots = len(snapshots)
    if num_snapshots >= 2:
        first_date = snapshots[0]["timestamp"][:10]
        last_date = snapshots[-1]["timestamp"][:10]
        snapshot_info = f"{num_snapshots} snapshots from {first_date} to {last_date}"
        mode_desc = "Downloads per year (computed from snapshot deltas)"
    else:
        snapshot_info = "1 snapshot (cumulative totals only)"
        mode_desc = "Cumulative download totals (run again later to get per-period deltas)"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BEAST2 Package Download Statistics</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px; background: #f5f5f5; color: #333; }}
        h1 {{ margin-bottom: 10px; }}
        .subtitle {{ color: #666; margin-bottom: 5px; }}
        .info {{ color: #888; margin-bottom: 5px; font-size: 13px; }}
        .chart-container {{ background: white; border-radius: 8px; padding: 20px; margin: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .toolbar {{ display: flex; gap: 10px; align-items: center; margin-bottom: 15px; flex-wrap: wrap; }}
        .toolbar input {{ padding: 8px 12px; width: 300px; max-width: 100%; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }}
        .toolbar button {{ padding: 8px 12px; border: 1px solid #ccc; border-radius: 4px; background: white; cursor: pointer; font-size: 13px; }}
        .toolbar button:hover {{ background: #eef3f9; }}
        table.main {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        table.main > thead th {{ background: #4e79a7; color: white; padding: 10px 12px; text-align: left; cursor: pointer; user-select: none; white-space: nowrap; }}
        table.main > thead th:hover {{ background: #3d6a99; }}
        td {{ padding: 8px 12px; border-bottom: 1px solid #eee; }}
        td.num, th.num, table.main > thead th.num {{ text-align: center; font-variant-numeric: tabular-nums; }}
        .summary-row {{ cursor: pointer; }}
        .summary-row:hover {{ background: #f0f7ff; }}
        .pkg-name {{ font-weight: 500; }}
        .caret {{ display: inline-block; width: 16px; color: #4e79a7; transition: transform 0.15s; }}
        tbody.open .caret {{ transform: rotate(90deg); }}
        tbody.open .summary-row {{ background: #eef3f9; }}
        .detail-row {{ display: none; }}
        tbody.open .detail-row {{ display: table-row; }}
        .detail-row > td {{ background: #fafbfc; padding: 12px 16px 16px 32px; }}
        .detail-meta {{ font-size: 13px; color: #666; margin-bottom: 8px; }}
        .detail-meta a {{ color: #4e79a7; }}
        table.versions {{ border-collapse: collapse; font-size: 13px; background: white; border: 1px solid #e3e3e3; }}
        table.versions th {{ background: #eef3f9; color: #333; padding: 6px 10px; text-align: center; font-weight: 600; white-space: nowrap; }}
        table.versions th:first-child, table.versions td:first-child {{ text-align: left; }}
        table.versions td {{ padding: 5px 10px; border-bottom: 1px solid #f0f0f0; }}
        table.versions tr.total td {{ font-weight: 600; border-top: 2px solid #ddd; }}
        td.zero, td.na {{ color: #ccc; }}
        td.date {{ color: #666; white-space: nowrap; }}
        td.partial {{ text-decoration: underline dotted #999; text-underline-offset: 3px; cursor: help; }}
        .badge {{ display: inline-block; margin-left: 8px; padding: 1px 7px; font-size: 11px; font-weight: 500; color: #8a5a00; background: #fff3cd; border: 1px solid #f0d58a; border-radius: 10px; cursor: help; vertical-align: 1px; }}
        .badge.new {{ color: #2d6a2d; background: #e6f4e6; border-color: #b6dcb6; }}
        .bar {{ display: inline-block; height: 8px; background: #4e79a7; border-radius: 2px; vertical-align: middle; margin-right: 6px; }}
        .sort-arrow {{ margin-left: 4px; font-size: 10px; }}
        table.main > thead th {{ position: relative; }}
        th.num .sort-arrow {{ position: absolute; margin-left: 6px; top: 50%; transform: translateY(-50%); }}
        .summary {{ margin-bottom: 20px; color: #555; }}
        .note {{ background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; padding: 12px 16px; margin-bottom: 20px; font-size: 13px; }}
    </style>
</head>
<body>
    <h1>BEAST2 Package Download Statistics</h1>
    <p class="info"><a href="dependencies.html">Package dependency network &rarr;</a></p>
    <p class="subtitle">{mode_desc}</p>
    <p class="info">Data: {snapshot_info}</p>
    {f"<p class='info'>The first snapshot of each package is used as its baseline: those downloads count towards <em>All-time</em> but not <em>Tracked</em>. Packages labelled <span class='badge'>from …</span> were first seen after {_fmt(global_start)}; dotted numbers are partial years (hover for the date).</p>" if is_delta else ""}
    <p class="summary">
        <strong>{len(sorted_packages)}</strong> packages &middot;
        <strong>{sum(pkg_totals.values()):,}</strong> .zip downloads in tracked period &middot;
        <strong>{sum(alltime_pkg.values()):,}</strong> all-time
    </p>

    {"<div class='note'>Only one snapshot exists. The table shows cumulative totals. Run the script again later (e.g., monthly) to get per-period download counts.</div>" if num_snapshots < 2 else ""}

    <div class="chart-container">
        <canvas id="downloadsChart" height="100"></canvas>
    </div>

    <div class="toolbar">
        <input type="text" id="searchInput" placeholder="Filter packages..." oninput="filterTable()">
        <button onclick="setAll(true)">Expand all</button>
        <button onclick="setAll(false)">Collapse all</button>
    </div>

    <table class="main" id="statsTable">
        <thead>
            <tr>
                {_th("Package", 0)}
                {_th("Tracked", 1)}
                {year_headers}
                {extra_headers}
            </tr>
        </thead>
        {''.join(table_rows)}
    </table>

    <script>
        const PERIODS = {json.dumps(periods)};
        const DETAILS = {json.dumps(details)};

        const ctx = document.getElementById('downloadsChart').getContext('2d');
        new Chart(ctx, {{
            type: 'bar',
            data: {{
                labels: {json.dumps(chart_packages)},
                datasets: {json.dumps(chart_datasets)}
            }},
            options: {{
                responsive: true,
                plugins: {{
                    title: {{ display: true, text: 'Top {top_n} Packages by Downloads' }},
                    legend: {{ position: 'top' }}
                }},
                scales: {{
                    x: {{ stacked: true, ticks: {{ maxRotation: 45 }} }},
                    y: {{ stacked: true, title: {{ display: true, text: 'Downloads' }} }}
                }}
            }}
        }});

        const fmt = n => n.toLocaleString('en-US');
        const esc = s => String(s).replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
        const cell = n => n === null
            ? '<td class="num na" title="Not yet tracked">–</td>'
            : `<td class="num${{n ? '' : ' zero'}}">${{fmt(n)}}</td>`;

        function renderDetail(pkg) {{
            const d = DETAILS[pkg];
            const maxTracked = Math.max(1, ...d.versions.map(v => v.tracked));
            const head = '<tr><th>Version</th><th>Released</th><th>Tracked</th>'
                + PERIODS.map(p => `<th>${{esc(p.label)}}</th>`).join('')
                + '<th>All-time</th></tr>';
            const rows = d.versions.map(v =>
                `<tr><td>${{esc(v.tag)}}</td><td class="num date">${{esc(v.published || '–')}}</td>`
                + `<td class="num"><span class="bar" style="width:${{Math.round(60 * v.tracked / maxTracked)}}px"></span>${{fmt(v.tracked)}}</td>`
                + v.periods.map(cell).join('')
                + cell(v.alltime) + '</tr>'
            ).join('');
            const tracked = d.versions.reduce((s, v) => s + v.tracked, 0);
            const alltime = d.versions.reduce((s, v) => s + v.alltime, 0);
            const total = `<tr class="total"><td>Total</td><td></td><td class="num">${{fmt(tracked)}}</td>`
                + d.periodTotals.map(cell).join('') + cell(alltime) + '</tr>';
            const repo = d.repo
                ? `<a href="https://github.com/${{esc(d.repo)}}/releases" target="_blank" rel="noopener">${{esc(d.repo)}}</a> &middot; `
                : '';
            const since = d.since ? ` &middot; tracked from ${{esc(d.since)}}` : '';
            const cban = d.cban ? ` &middot; on CBAN since ${{esc(d.cban)}}` : '';
            return `<div class="detail-meta">${{repo}}${{d.versions.length}} version${{d.versions.length === 1 ? '' : 's'}}${{since}}${{cban}}</div>`
                + `<table class="versions"><thead>${{head}}</thead><tbody>${{rows}}${{total}}</tbody></table>`;
        }}

        function toggle(tb, force) {{
            const open = force === undefined ? !tb.classList.contains('open') : force;
            if (open && !tb.dataset.rendered) {{
                tb.querySelector('.detail-row td').innerHTML = renderDetail(tb.dataset.pkg);
                tb.dataset.rendered = '1';
            }}
            tb.classList.toggle('open', open);
        }}

        function setAll(open) {{
            document.querySelectorAll('#statsTable tbody.pkg').forEach(tb => {{
                if (tb.style.display !== 'none') toggle(tb, open);
            }});
        }}

        function filterTable() {{
            const query = document.getElementById('searchInput').value.toLowerCase();
            document.querySelectorAll('#statsTable tbody.pkg').forEach(tb => {{
                tb.style.display = tb.dataset.pkg.toLowerCase().includes(query) ? '' : 'none';
            }});
        }}

        let sortDir = {{}};
        function sortTable(colIdx) {{
            const table = document.getElementById('statsTable');
            const groups = Array.from(table.querySelectorAll('tbody.pkg'));
            const dir = sortDir[colIdx] = !(sortDir[colIdx] || false);
            const val = tb => colIdx === 0 ? tb.dataset.pkg
                : tb.querySelector('.summary-row').cells[colIdx].textContent.replace(/,/g, '').trim();

            groups.sort((a, b) => {{
                let aVal = val(a), bVal = val(b);
                if (colIdx > 0) {{
                    aVal = parseInt(aVal) || 0;
                    bVal = parseInt(bVal) || 0;
                    return dir ? aVal - bVal : bVal - aVal;
                }}
                return dir ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
            }});

            groups.forEach(tb => table.appendChild(tb));
        }}
    </script>
</body>
</html>"""

    with open(filename, "w") as f:
        f.write(html)
    print(f"HTML report written to {filename}")


def main():
    parser = argparse.ArgumentParser(
        description="Collect BEAST2 package download statistics from GitHub releases (snapshot-based)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Take a new snapshot (fetches current download counts):
  python beast2_release_stats.py

  # Generate a report from existing snapshots without fetching new data:
  python beast2_release_stats.py --report

  # Specify custom output files:
  python beast2_release_stats.py --output-csv stats.csv --output-html stats.html
        """,
    )
    parser.add_argument("--report", action="store_true",
                        help="Only generate report from existing snapshots (no new API calls)")
    parser.add_argument("--snapshots-dir", default=SNAPSHOTS_DIR,
                        help=f"Directory to store snapshots (default: {SNAPSHOTS_DIR})")
    parser.add_argument("--output-csv", default="beast2_download_stats.csv",
                        help="Output CSV filename")
    parser.add_argument("--output-html", default="beast2_download_stats.html",
                        help="Output HTML filename")
    parser.add_argument("--cban-dir", default=None,
                        help="Local clone of CompEvol/CBAN (created if missing) used to look up "
                             "when each package was added to CBAN. Omit to skip.")
    parser.add_argument("--backfill-dates", action="store_true",
                        help="Fetch missing release dates into the latest snapshot (one-off; needs GITHUB_TOKEN)")
    parser.add_argument("--force", action="store_true",
                        help="Take a snapshot even if one exists for this month")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")

    if not args.report:
        # Take a new snapshot
        if not args.force and snapshot_exists_this_month(args.snapshots_dir):
            print(f"Snapshot for {datetime.now():%Y-%m} already exists. Skipping.")
            sys.exit(0)

        if not token:
            print("WARNING: No GITHUB_TOKEN set. API rate limit will be 60 requests/hour.")
            print("         Set GITHUB_TOKEN environment variable for 5000 requests/hour.")
            print()

        # Fetch and parse package XML files
        all_repos = {}
        for url in CBAN_URLS:
            try:
                xml_content = fetch_xml(url)
                repos = extract_repos_from_xml(xml_content)
                for repo_key, pkg_name in repos.items():
                    if repo_key not in all_repos:
                        all_repos[repo_key] = pkg_name
            except Exception as e:
                print(f"Error fetching {url}: {e}")

        print(f"\nFound {len(all_repos)} unique GitHub repositories\n")

        # Collect current counts
        snapshot = collect_snapshot(all_repos, token)
        if not snapshot:
            print("No data collected.")
            sys.exit(1)

        save_snapshot(snapshot, args.snapshots_dir)

    if args.backfill_dates:
        backfill_release_dates(args.snapshots_dir, token)

    # Load all snapshots and generate report
    snapshots = load_snapshots(args.snapshots_dir)
    if not snapshots:
        print("No snapshots found. Run without --report first.")
        sys.exit(1)

    print(f"\nLoaded {len(snapshots)} snapshot(s)")

    if len(snapshots) >= 2:
        stats = compute_deltas(snapshots)
        is_delta = True
        print(f"Computed deltas: {len(stats)} entries across {len(snapshots)-1} period(s)")
    else:
        stats = compute_cumulative_by_year(snapshots)
        is_delta = False
        print("Only 1 snapshot available — showing cumulative totals.")
        print("Run this script again later to get per-period download deltas.")

    if not stats:
        print("No download data to report.")
        sys.exit(1)

    write_csv(stats, args.output_csv, is_delta=is_delta)
    cban_added = cban_added_dates(args.cban_dir) if args.cban_dir else None
    generate_html(stats, args.output_html, snapshots, is_delta=is_delta, cban_added=cban_added)

    total_downloads = sum(s["zip_downloads_delta"] for s in stats)
    print(f"\nDone! Total .zip downloads reported: {total_downloads:,}")


if __name__ == "__main__":
    main()
