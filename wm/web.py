"""Web access for the AI: search the web and read pages as clean text - small enough not to waste tokens.
No account or API key needed (search uses DuckDuckGo's plain HTML page). Turn it off with: windmill settings web_access false"""
import html
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 Windmill")
PAGE_CHARS = 6000   # one "part" of a page - about 1,500 tokens
MAX_BYTES = 3_000_000


def _get(url, data=None, timeout=25):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8",
                                                          "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(MAX_BYTES)
        ctype = r.headers.get("Content-Type", "")
        enc = (re.search(r"charset=([\w-]+)", ctype) or [None, "utf-8"])[1]
        return raw.decode(enc, errors="replace"), ctype, r.geturl()


# ------------------------------------------------------------------ search
def _unwrap(href):
    """DuckDuckGo wraps result links: //duckduckgo.com/l/?uddg=<real url>"""
    q = urllib.parse.urlparse(href if href.startswith("http") else "https:" + href)
    real = urllib.parse.parse_qs(q.query).get("uddg")
    return real[0] if real else (href if href.startswith("http") else "https:" + href)


def _clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def search(query, k=6):
    """Top results as [{title, url, snippet}]."""
    page, _, _ = _get("https://html.duckduckgo.com/html/", data=urllib.parse.urlencode({"q": query}).encode())
    out = []
    starts = list(re.finditer(r'<a\s([^>]*class="[^"]*\bresult__a\b[^"]*"[^>]*)>(.*?)</a>', page, re.S))
    for i, m in enumerate(starts):
        attrs, title = m.groups()
        hm = re.search(r'href="([^"]+)"', attrs)
        if not hm:
            continue
        href = hm.group(1)
        rest = page[m.end(): starts[i + 1].start() if i + 1 < len(starts) else len(page)]
        snip = re.search(r'class="[^"]*\bresult__snippet\b[^"]*"[^>]*>(.*?)</(?:a|div|td)>', rest, re.S)
        url = _unwrap(html.unescape(href))
        if "duckduckgo.com/y.js" in url:  # ads
            continue
        out.append({"title": _clean(title), "url": url, "snippet": _clean(snip.group(1)) if snip else ""})
        if len(out) >= k:
            break
    if not out:  # the lite page has a simpler layout
        page, _, _ = _get("https://lite.duckduckgo.com/lite/", data=urllib.parse.urlencode({"q": query}).encode())
        for m in re.finditer(r"<a[^>]+href=\"([^\"]+)\"[^>]*class=['\"]result-link['\"][^>]*>(.*?)</a>", page, re.S):
            out.append({"title": _clean(m.group(2)), "url": _unwrap(html.unescape(m.group(1))), "snippet": ""})
            if len(out) >= k:
                break
    return out


def search_text(query):
    try:
        res = search(query)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return f"ERROR: web search failed ({e}). The internet may be unreachable."
    if not res:
        return "No results. Try different words."
    return "\n".join(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet'][:240]}" for i, r in enumerate(res, 1)) + \
        "\n\nRead a result with fetch_url(url)."


# ------------------------------------------------------------------ pages -> readable text
class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe", "button", "template",
            "select", "dialog"}
    BLOCK = {"p", "div", "section", "article", "main", "br", "tr", "table", "ul", "ol", "dl", "blockquote", "figure", "hr",
             "dt", "dd"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.out, self.title, self.in_title = base, [], "", False
        self.stack = []  # (tag, hidden) for every open element - hidden parts are dropped with all they contain
        self.pre, self.href, self.href_at = 0, None, 0

    @property
    def hidden(self):
        return any(h for _, h in self.stack)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        hide = tag in self.SKIP or a.get("aria-hidden") == "true" or a.get("hidden") is not None and "hidden" in a \
            or "display:none" in (a.get("style") or "").replace(" ", "")
        if tag not in self.VOID:
            self.stack.append((tag, hide))
        if tag == "title":
            self.in_title = True
        if hide or self.hidden:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag == "pre":
            self.pre += 1
            self.out.append("\n```\n")
        elif tag == "code" and not self.pre:
            self.out.append("`")
        elif tag == "a" and a.get("href") and not a["href"].startswith(("#", "javascript:")):
            self.href, self.href_at = urllib.parse.urljoin(self.base, a["href"]), len(self.out)
        elif tag == "img" and a.get("alt"):
            self.out.append(f"[image: {a['alt']}]")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if not any(t == tag for t, _ in self.stack):
            return
        was_hidden = self.hidden
        while self.stack:  # close up to the matching tag (tolerates sloppy HTML)
            t, _ = self.stack.pop()
            if t == tag:
                break
        if was_hidden:
            return
        if tag == "pre":
            self.pre = max(0, self.pre - 1)
            self.out.append("\n```\n")
        elif tag == "code" and not self.pre:
            self.out.append("`")
        elif tag == "a" and self.href:
            label = "".join(self.out[self.href_at:]).strip().rstrip("/")
            if len(self.href) < 160 and label and label not in self.href:
                self.out.append(f" ({self.href})")
            self.href = None
        elif tag in self.BLOCK or tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n")

    def handle_data(self, d):
        if self.in_title:
            self.title += d
            return
        if self.hidden:
            return
        self.out.append(d if self.pre else re.sub(r"\s+", " ", d))


def page_text(url):
    """(title, text) for a web page, without menus, scripts and styling."""
    body, ctype, final = _get(url)
    if "html" not in ctype and not body.lstrip().startswith("<"):
        return final, body  # plain text, JSON, markdown, source code...
    p = _Text(final)
    p.feed(body)
    text = "".join(p.out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return (p.title.strip() or final), text


def fetch_text(url, part=1):
    url = (url or "").strip().strip("<>\"'")
    if not re.match(r"^https?://", url):
        url = "https://" + url
    try:
        title, text = page_text(url)
    except urllib.error.HTTPError as e:
        return f"ERROR: {url} answered {e.code} {e.reason}."
    except (urllib.error.URLError, OSError, ValueError) as e:
        return f"ERROR: couldn't open {url} ({e})."
    if not text.strip():
        return (f"{title}\n(The page has no readable text - it's probably built by JavaScript. "
                "Try a different page, the site's docs, or the Playwright connector to view it in a browser.)")
    parts = max(1, -(-len(text) // PAGE_CHARS))
    part = min(max(1, int(part or 1)), parts)
    chunk = text[(part - 1) * PAGE_CHARS: part * PAGE_CHARS]
    more = f"\n\n(part {part} of {parts} - fetch_url(url, part={part + 1}) for more)" if part < parts else ""
    return f"# {title}\n{url}\n\n{chunk}{more}"
