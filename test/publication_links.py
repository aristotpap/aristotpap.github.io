#!/usr/bin/env python

"""Check every link in the publication list of a built publications page.

Usage:
  python test/publication_links.py <site>/publications/index.html   # a built or deployed site
  python test/publication_links.py https://aristotpap.github.io/publications/

arXiv papers are checked through the arXiv API, DOIs through the DOI handle API and YouTube/Vimeo
videos through oEmbed, because their web pages load fine even when a paper or video does not exist.
Site-relative links must exist as files in the site; anything else gets an HTTP request.

Exits 1 when a link is definitely broken (not found, removed, missing file). Sites that cannot be
verified right now (bot blocks, rate limits, timeouts) only produce a warning, so the check does
not fail on someone else's outage.

Uses only the standard library.
"""

import http.client
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (compatible; publication-link-check; +https://github.com/aristotpap/aristotpap.github.io)"
NETWORK_ERRORS = (OSError, http.client.HTTPException, ValueError)  # URLError and timeouts are OSErrors
ARXIV_HOSTS = ("arxiv.org", "www.arxiv.org", "export.arxiv.org")
# /abs/2405.19452v2, /pdf/arXiv:2405.19452.pdf, /abs/math.GT/0309136 (old-style, with an optional subject class).
# Not /html/: arXiv has HTML for only some versions, so those links get a plain HTTP check.
ARXIV_PATH = re.compile(r"/(?:abs|pdf)/(?:arxiv:)?(?:(\d{4}\.\d{4,5})|([a-z-]+)(?:\.[a-z]{2})?/(\d{7}))(?:v(\d+))?(?:\.pdf)?/?", re.IGNORECASE)
DOI = re.compile(r"^https?://(?:dx\.)?doi\.org/(10\..+)$")
YOUTUBE = re.compile(r"^https?://(?:www\.|m\.)?(?:youtube(?:-nocookie)?\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/|v/)|youtu\.be/)([\w-]{11})")
VIMEO = re.compile(r"^https?://(?:www\.|player\.)?vimeo\.com/(?:video/)?\d+")
# Certificate problems a browser would also reject: not yet valid, expired, self-signed, wrong host. Others, such as a
# missing intermediate certificate (20) that browsers fetch themselves, only mean Python could not verify the chain.
BAD_CERTIFICATES = {9, 10, 18, 19, 62}
OK, BROKEN, UNVERIFIED = "ok", "broken", "unverified"


class PublicationLinks(HTMLParser):
    """Collect (paper title, link text, href) for every link inside <div class="publications">."""

    def __init__(self) -> None:
        super().__init__()
        self.links = []  # [entry, text, href]; entry["title"] is filled in as the entry is parsed
        self.depth = 0  # nesting depth of <div>s inside the publication list
        self.entry = {"title": ""}
        self.in_title = False
        self.link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "div":
            if self.depth or "publications" in classes:
                self.depth += 1
            self.in_title = bool(self.depth) and "title" in classes
        elif tag == "li" and self.depth:
            self.entry = {"title": ""}  # one <li> per paper; its venue badge link comes before the title
        elif tag == "a" and self.depth and attrs.get("href"):
            self.link = [self.entry, "", attrs["href"].strip()]

    def handle_endtag(self, tag):
        if tag == "div" and self.depth:
            self.depth -= 1
            self.in_title = False
        elif tag == "a" and self.link:
            self.links.append(self.link)
            self.link = None

    def handle_data(self, data):
        if self.in_title:
            self.entry["title"] += data
        if self.link:
            self.link[1] += data

    def results(self) -> list:
        return [(" ".join(entry["title"].split()), " ".join(text.split()), href) for entry, text, href in self.links]


def fetch(url: str, timeout: int = 30) -> tuple:
    """GET a URL and return (HTTP status, body), following redirects. Network errors propagate."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(2_000_000)
    except urllib.error.HTTPError as e:
        return e.code, b""


def check_http(url: str) -> tuple:
    """404/410, unknown hosts and certificates a browser would reject are broken; other failures are unverified."""
    for attempt in range(2):
        try:
            status, _ = fetch(url)
        except urllib.error.URLError as e:
            reason = e.reason
            if isinstance(reason, ssl.SSLCertVerificationError) and reason.verify_code in BAD_CERTIFICATES:
                return BROKEN, f"invalid TLS certificate ({reason.verify_message})"
            if isinstance(reason, socket.gaierror) and reason.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", None)):
                result = (BROKEN, "host not found")
            else:  # includes temporary DNS failures (EAI_AGAIN) and certificate chains Python cannot complete
                result = (UNVERIFIED, f"request failed ({reason})")
        except NETWORK_ERRORS as e:
            result = (UNVERIFIED, f"request failed ({e!r})")
        else:
            if 200 <= status < 300:
                return OK, f"HTTP {status}"
            if status in (404, 410):
                return BROKEN, f"HTTP {status}"
            if 300 <= status < 400:  # urllib follows redirects, so a 3xx here is a redirect loop or a redirect without a target
                result = (UNVERIFIED, f"HTTP {status}; the redirect does not resolve")
            else:
                result = (UNVERIFIED, f"HTTP {status}; the site may be blocking automated requests")
        if attempt == 0:
            time.sleep(5)
    return result


def arxiv_paper(url: str) -> tuple | None:
    """(arXiv ID, version or None) for a link to an arXiv paper, e.g. ('2405.19452', 2); None for other links."""
    parsed = urllib.parse.urlparse(url)
    match = ARXIV_PATH.fullmatch(parsed.path) if parsed.netloc.lower() in ARXIV_HOSTS else None
    if not match:
        return None
    new_style, archive, number, version = match.groups()
    return new_style or f"{archive.lower()}/{number}", int(version) if version else None


def latest_arxiv_version(arxiv_id: str) -> tuple:
    """(latest version number, None) if the arXiv API knows the paper, (0, None) if not, (None, detail) if unclear."""
    atom = "{http://www.w3.org/2005/Atom}"
    for attempt in range(3):
        time.sleep(3 if attempt == 0 else 30)  # arXiv asks API clients to wait 3 seconds between requests
        try:
            status, body = fetch("https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": arxiv_id}))
            if status != 200:
                detail = f"arXiv API returned HTTP {status}"
                continue
            entry_ids = [entry.findtext(atom + "id") or "" for entry in ET.fromstring(body).iter(atom + "entry")]
        except (*NETWORK_ERRORS, ET.ParseError) as e:
            detail = f"arXiv API unreachable ({e!r})"
            continue
        versions = [int(m[1]) for entry_id in entry_ids if (m := re.search(r"/" + re.escape(arxiv_id) + r"v(\d+)$", entry_id))]
        return (max(versions), None) if versions else (0, None)
    return None, detail


def check_arxiv(arxiv_id: str, version: int | None, cache: dict) -> tuple:
    """The paper exists if the arXiv API returns it (it returns nothing for unknown IDs), the version if it is not newer."""
    if "arxiv:" + arxiv_id not in cache:
        cache["arxiv:" + arxiv_id] = latest_arxiv_version(arxiv_id)
    latest, detail = cache["arxiv:" + arxiv_id]
    if latest is None:
        return UNVERIFIED, detail
    if not latest:
        return BROKEN, f"no arXiv paper {arxiv_id}"
    if version is not None and not 1 <= version <= latest:
        return BROKEN, f"arXiv {arxiv_id} has no version {version} (latest is v{latest})"
    return OK, "arXiv paper exists"


def check_doi(doi: str) -> tuple:
    """The DOI handle API answers responseCode 1 for registered DOIs and 100 for unknown ones."""
    try:
        status, body = fetch("https://doi.org/api/handles/" + urllib.parse.quote(urllib.parse.unquote(doi), safe="/"))
        code = json.loads(body).get("responseCode") if body else None
    except NETWORK_ERRORS as e:
        return UNVERIFIED, f"DOI API unreachable ({e!r})"
    if code == 1:
        return OK, "DOI is registered"
    if code == 100 or status == 404:
        return BROKEN, f"DOI {doi} is not registered"
    return UNVERIFIED, f"DOI API returned HTTP {status}"


def check_oembed(endpoint: str, video_url: str, not_found: tuple) -> tuple:
    """Video pages load even for removed videos, so ask the platform's oEmbed API instead."""
    try:
        status, _ = fetch(endpoint + urllib.parse.quote(video_url, safe=""))
    except NETWORK_ERRORS as e:
        return UNVERIFIED, f"oEmbed unreachable ({e!r})"
    if status == 200:
        return OK, "video is public"
    if status in not_found:
        return BROKEN, f"video not found (oEmbed HTTP {status})"
    return UNVERIFIED, f"oEmbed HTTP {status}; the video may be private or have embedding turned off"


def check_url(url: str, cache: dict) -> tuple:
    """Check an absolute URL once, picking the most reliable check for where it points."""
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        return OK, "not an http(s) link, not checked"
    if paper := arxiv_paper(url):
        return check_arxiv(*paper, cache)
    if match := DOI.match(url):
        key, check = "doi:" + match[1].lower(), lambda: check_doi(match[1])
    elif match := YOUTUBE.match(url):
        watch = f"https://www.youtube.com/watch?v={match[1]}"
        key, check = "youtube:" + match[1], lambda: check_oembed("https://www.youtube.com/oembed?format=json&url=", watch, (400, 404))
    elif VIMEO.match(url):  # pass the whole link: unlisted videos need the hash after the ID (/<hash> or ?h=<hash>)
        video = url.split("#")[0]
        key, check = video, lambda: check_oembed("https://vimeo.com/api/oembed.json?url=", video, (404,))
    else:
        key, check = url, lambda: check_http(url)
    if key not in cache:
        cache[key] = check()
    return cache[key]


def check_file(path: Path) -> tuple:
    path = path / "index.html" if path.is_dir() else path
    return (OK, "file exists") if path.is_file() else (BROKEN, f"missing file {path}")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    page = sys.argv[1]
    live = bool(re.match(r"^https?://", page))
    if live:
        status, body = fetch(page)
        if status != 200:
            sys.exit(f"Could not load {page}: HTTP {status}")
        html = body.decode("utf-8", "replace")
    else:
        page_path = Path(page).resolve()
        html = page_path.read_text(encoding="utf-8")
        site_root = page_path.parent.parent  # <site>/publications/index.html -> <site>

    parser = PublicationLinks()
    parser.feed(html)
    links = [link for link in parser.results() if not link[2].startswith(("#", "mailto:", "javascript:"))]
    if not links:
        sys.exit(f"No publication links found in {page}; is it the publications page?")

    cache, results = {}, []
    for title, text, href in links:
        if live or urllib.parse.urlparse(href).scheme or urllib.parse.urlparse(href).netloc:  # netloc: //host/path links
            status, detail = check_url(urllib.parse.urljoin(page if live else "https:", href), cache)
        else:  # a link within the site: the file must exist in the site being checked
            path = urllib.parse.unquote(urllib.parse.urlparse(href).path)
            status, detail = check_file(site_root / path.lstrip("/") if path.startswith("/") else page_path.parent / path)
        results.append((status, title, text, href, detail))
        print(f"{status.upper():10} {title[:48]:48} {text[:8]:8} {href}  ({detail})")

    broken = [r for r in results if r[0] == BROKEN]
    unverified = [r for r in results if r[0] == UNVERIFIED]
    print(f"\n{len(results)} links: {len(results) - len(broken) - len(unverified)} ok, {len(unverified)} unverified, {len(broken)} broken")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(f"### Publication links\n\n{len(results)} links: {len(broken)} broken, {len(unverified)} unverified\n\n")
            if broken or unverified:
                f.write("| Result | Paper | Link | Detail |\n|---|---|---|---|\n")
                for status, title, text, href, detail in broken + unverified:
                    f.write(f"| {status} | {title} | [{text or href}]({href}) | {detail} |\n")
    sys.exit(1 if broken else 0)


if __name__ == "__main__":
    main()
