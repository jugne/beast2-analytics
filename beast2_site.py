"""
Shared site chrome for the generated BEAST2 analytics pages.

Both report generators import this so the pages share one header with
tab-style navigation. Colours fall back to the stats page's light palette
but pick up CSS variables (--surface, --border, --text, ...) when the page
defines them, so the header also follows the dependency page's dark mode.
"""

SITE_TITLE = "BEAST2 Package Analytics"

PAGES = [
    ("index.html", "Download statistics"),
    ("dependencies.html", "Dependency network"),
]

NAV_CSS = """
    .site-header { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
        gap: 8px 24px; margin: -20px -20px 20px; padding: 0 20px;
        background: var(--surface, #fff); border-bottom: 1px solid var(--border, #e0e0e0); }
    .site-header .brand { font-size: 15px; font-weight: 600; color: var(--text, #333);
        text-decoration: none; padding: 14px 0; white-space: nowrap; }
    .site-header .brand span { color: var(--muted, #888); font-weight: 400; }
    .site-nav { display: flex; gap: 4px; }
    .site-nav a { display: block; padding: 14px 14px 12px; font-size: 14px; color: var(--text-2, #555);
        text-decoration: none; border-bottom: 2px solid transparent; white-space: nowrap; }
    .site-nav a:hover { color: var(--text, #111); border-bottom-color: var(--border, #ccc); }
    .site-nav a[aria-current="page"] { color: var(--library, #4e79a7); font-weight: 600;
        border-bottom-color: var(--library, #4e79a7); }
    @media (max-width: 600px) { .site-header { justify-content: flex-start; } .site-header .brand { padding-bottom: 0; } }
"""


def nav_html(active):
    """Return the header markup, marking the page whose filename is `active`."""
    links = "".join(
        f'<a href="{href}"{" aria-current=\"page\"" if href == active else ""}>{label}</a>'
        for href, label in PAGES
    )
    return (
        '<header class="site-header">'
        f'<a class="brand" href="index.html">{SITE_TITLE}</a>'
        f'<nav class="site-nav">{links}</nav>'
        '</header>'
    )
