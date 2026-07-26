"""Build the self-contained HTML viewers for the Tech_reference_docs markdown.

Each doc is a `.md` source plus an `.html` viewer shell that renders it via
marked.js + mermaid.js (CDN). The markdown is embedded inside the shell in a
``<script type="text/markdown" id="source-md">`` block.

The primary path SWAPS that block in the existing `.html` rather than
re-rendering from ``HTML_TEMPLATE``. The live shells carry hand-edits the
template never received (sidebar width, title), so re-rendering would silently
revert them. ``HTML_TEMPLATE`` is the fallback used only to bootstrap a doc whose
`.html` does not exist yet.

Run from the repo root:
    python3 -m scripts.build_reference_docs
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TECH_DOCS = ROOT / "AI_Agents" / "Reference_docs" / "Tech_reference_docs"

MARKER_OPEN = '<script type="text/markdown" id="source-md">'
MARKER_CLOSE = "</script>"

# doc key -> markdown source. The .html sibling is the build target.
DOCS: dict[str, Path] = {
    "architecture": _TECH_DOCS / "ARCHITECTURE.md",
    "chat_flow": _TECH_DOCS / "CHAT_FLOW.md",
}


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Prozpr Backend — Architecture Walkthrough</title>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<style>
  :root {
    --bg: #fbfaf7;
    --panel: #ffffff;
    --ink: #1a1a1a;
    --muted: #6b6b6b;
    --rule: #e6e3dc;
    --accent: #b14a18;
    --link: #1d6fb8;
    --code-bg: #f3efe7;
    --sidebar-bg: #f6f3ec;
    --sidebar-w: 280px;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
    line-height: 1.65;
    font-size: 16px;
    -webkit-font-smoothing: antialiased;
  }
  .layout { display: flex; min-height: 100vh; }
  nav.toc {
    width: var(--sidebar-w);
    background: var(--sidebar-bg);
    border-right: 1px solid var(--rule);
    padding: 28px 22px;
    position: sticky;
    top: 0;
    height: 100vh;
    overflow-y: auto;
    font-size: 14px;
    flex-shrink: 0;
  }
  nav.toc h3 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--muted);
    margin: 0 0 14px 0;
    font-weight: 600;
  }
  nav.toc ul { list-style: none; margin: 0; padding: 0; }
  nav.toc li { margin: 4px 0; }
  nav.toc li.lvl-3 { margin-left: 14px; font-size: 13px; }
  nav.toc a {
    color: var(--ink);
    text-decoration: none;
    display: block;
    padding: 4px 6px;
    border-radius: 4px;
    line-height: 1.35;
  }
  nav.toc a:hover { background: rgba(177, 74, 24, 0.08); color: var(--accent); }
  nav.toc a.active { background: rgba(177, 74, 24, 0.12); color: var(--accent); font-weight: 500; }

  main {
    flex: 1;
    padding: 56px 64px 120px;
    max-width: calc(960px + 128px);
    margin: 0 auto;
  }
  article { max-width: 800px; margin: 0 auto; }
  article h1 {
    font-size: 32px;
    line-height: 1.25;
    margin: 0 0 8px 0;
    letter-spacing: -0.01em;
  }
  article h2 {
    font-size: 22px;
    margin: 48px 0 16px 0;
    padding-top: 12px;
    border-top: 1px solid var(--rule);
    scroll-margin-top: 24px;
  }
  article h3 {
    font-size: 17px;
    margin: 28px 0 10px 0;
    scroll-margin-top: 24px;
  }
  article p { margin: 14px 0; }
  article a { color: var(--link); text-decoration: none; border-bottom: 1px solid rgba(29, 111, 184, 0.25); }
  article a:hover { border-bottom-color: var(--link); }
  article strong { color: var(--ink); }
  article ul, article ol { padding-left: 22px; }
  article li { margin: 6px 0; }
  article blockquote {
    margin: 18px 0;
    padding: 12px 18px;
    border-left: 3px solid var(--accent);
    background: #faf5ee;
    color: #3a3a3a;
    border-radius: 0 6px 6px 0;
  }
  article blockquote p { margin: 6px 0; }
  article code {
    background: var(--code-bg);
    padding: 2px 6px;
    border-radius: 4px;
    font-family: "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
    font-size: 13.5px;
  }
  article pre {
    background: #1f1d1a;
    color: #f5f1e8;
    padding: 18px 20px;
    border-radius: 8px;
    overflow-x: auto;
    font-size: 13px;
    line-height: 1.5;
  }
  article pre code { background: transparent; padding: 0; color: inherit; }
  article table {
    border-collapse: collapse;
    margin: 18px 0;
    width: 100%;
    font-size: 14px;
  }
  article th, article td {
    border: 1px solid var(--rule);
    padding: 8px 12px;
    text-align: left;
    vertical-align: top;
  }
  article th { background: var(--sidebar-bg); font-weight: 600; }
  article tr:nth-child(even) td { background: #fafaf6; }
  article hr { border: none; border-top: 1px solid var(--rule); margin: 36px 0; }

  .mermaid {
    background: var(--panel);
    border: 1px solid var(--rule);
    border-radius: 8px;
    padding: 20px;
    margin: 22px 0;
    text-align: center;
    overflow-x: auto;
  }
  .mermaid svg { max-width: 100%; height: auto; }

  .header-strip {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 32px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--rule);
  }
  .header-strip .meta {
    font-size: 13px;
    color: var(--muted);
  }
  .header-strip .meta a { color: var(--accent); border-bottom-color: rgba(177, 74, 24, 0.3); }

  @media (max-width: 900px) {
    nav.toc { display: none; }
    main { padding: 32px 20px 80px; }
    article h1 { font-size: 26px; }
  }
</style>
</head>
<body>
<div class="layout">
  <nav class="toc">
    <h3>Contents</h3>
    <ul id="toc-list"></ul>
  </nav>
  <main>
    <div class="header-strip">
      <div></div>
      <div class="meta">
        Source: <a href="ARCHITECTURE.md">ARCHITECTURE.md</a> · Rebuild: <code>python3 -m scripts.build_reference_docs</code>
      </div>
    </div>
    <article id="content"></article>
  </main>
</div>

<script type="text/markdown" id="source-md">
__MARKDOWN_CONTENT__
</script>

<script>
(function () {
  const mdSource = document.getElementById('source-md').textContent;

  // 1. Configure marked. Use GitHub-style heading IDs.
  marked.use({
    gfm: true,
    breaks: false,
    headerIds: true,
    mangle: false,
  });

  // 2. Render markdown to HTML.
  const rawHtml = marked.parse(mdSource);
  const content = document.getElementById('content');
  content.innerHTML = rawHtml;

  // 3. Convert <pre><code class="language-mermaid"> blocks into <div class="mermaid">.
  content.querySelectorAll('pre code.language-mermaid').forEach((codeEl) => {
    const src = codeEl.textContent;
    const div = document.createElement('div');
    div.className = 'mermaid';
    div.textContent = src;
    codeEl.parentElement.replaceWith(div);
  });

  // 4. Ensure every h2/h3 has an id (marked usually does; belt-and-braces).
  function slugify(text) {
    return text.toLowerCase()
      .replace(/[^a-z0-9\s-]/g, '')
      .trim()
      .replace(/\s+/g, '-');
  }
  content.querySelectorAll('h2, h3').forEach((h) => {
    if (!h.id) h.id = slugify(h.textContent);
  });

  // 5. Build sidebar TOC from h2 + h3.
  const tocList = document.getElementById('toc-list');
  content.querySelectorAll('h2, h3').forEach((h) => {
    const li = document.createElement('li');
    li.className = 'lvl-' + h.tagName.charAt(1);
    const a = document.createElement('a');
    a.href = '#' + h.id;
    a.textContent = h.textContent;
    li.appendChild(a);
    tocList.appendChild(li);
  });

  // 6. Initialise mermaid AFTER blocks are in the DOM.
  mermaid.initialize({
    startOnLoad: false,
    theme: 'default',
    flowchart: { curve: 'basis', htmlLabels: true },
    securityLevel: 'loose',
  });
  mermaid.run({ querySelector: '.mermaid' });

  // 7. Active-section highlighting in the sidebar as the user scrolls.
  const headings = Array.from(content.querySelectorAll('h2, h3'));
  const tocLinks = Array.from(tocList.querySelectorAll('a'));
  const linkById = new Map(tocLinks.map(a => [a.getAttribute('href').slice(1), a]));

  function onScroll() {
    const fromTop = window.scrollY + 80;
    let active = headings[0];
    for (const h of headings) {
      if (h.offsetTop <= fromTop) active = h; else break;
    }
    tocLinks.forEach(a => a.classList.remove('active'));
    if (active) {
      const link = linkById.get(active.id);
      if (link) link.classList.add('active');
    }
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
})();
</script>
</body>
</html>
"""


def extract_markdown(html: str) -> str:
    """Return the markdown embedded in the source-md block."""
    start = html.find(MARKER_OPEN)
    if start == -1:
        raise ValueError(f"marker not found: {MARKER_OPEN}")
    start += len(MARKER_OPEN)
    end = html.find(MARKER_CLOSE, start)
    if end == -1:
        raise ValueError("unterminated source-md block")
    return html[start:end].strip("\n")


def replace_markdown(html: str, markdown: str) -> str:
    """Swap the source-md block's content, leaving the shell byte-identical.

    The markdown sits inside a <script> element, so a literal "</script" in the
    source would terminate the block early and break the page.
    """
    if "</script" in markdown.lower():
        raise ValueError(
            "markdown contains a literal '</script' which would break the HTML "
            "embedding. Rephrase before rebuilding."
        )
    start = html.find(MARKER_OPEN)
    if start == -1:
        raise ValueError(f"marker not found: {MARKER_OPEN}")
    body_start = start + len(MARKER_OPEN)
    end = html.find(MARKER_CLOSE, body_start)
    if end == -1:
        raise ValueError("unterminated source-md block")
    return f"{html[:body_start]}\n{markdown}\n{html[end:]}"


def build(key: str) -> Path:
    """Regenerate one doc's .html from its .md. Returns the html path."""
    md_path = DOCS[key]
    html_path = md_path.with_suffix(".html")
    if not md_path.exists():
        raise SystemExit(f"Source markdown not found: {md_path}")
    md = md_path.read_text(encoding="utf-8").strip("\n")

    if html_path.exists():
        # Preserve the existing shell exactly — it carries hand-edits the
        # bundled HTML_TEMPLATE does not have (sidebar width, title).
        html = replace_markdown(html_path.read_text(encoding="utf-8"), md)
    else:
        if "</script" in md.lower():
            raise SystemExit(f"{md_path.name} contains a literal '</script'.")
        html = HTML_TEMPLATE.replace("__MARKDOWN_CONTENT__", md)

    html_path.write_text(html, encoding="utf-8")
    print(f"Wrote {html_path.relative_to(ROOT)} ({len(html):,} bytes)")
    return html_path


def main() -> None:
    for key in DOCS:
        build(key)


if __name__ == "__main__":
    main()
