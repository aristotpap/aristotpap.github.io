#!/usr/bin/env python

"""Add new papers from Semantic Scholar to _bibliography/papers.bib and keep their videos current.

Reads the author ID from `semanticscholar_id` in _data/socials.yml, fetches the author's papers
and adds a BibTeX entry for every paper not already in papers.bib (matched by Semantic Scholar
paper ID, DOI, arXiv ID or title).

A paper's video is the one its newest arXiv version links: in its full text (figure captions often
say "Video: <url>"), abstract or comments. Videos that follow the paper are marked `video_auto =
{true}` (hidden from the BibTeX box); whenever the newest version links a different video, the job
switches them to it. A video without the marker is replaced only if an older version linked it.
Apart from that, existing entries are never changed, so hand edits such as links, thumbnails,
`selected`, or a hand-picked video (a talk recording, say) survive every run.

Uses only the standard library. Set S2_API_KEY to send a Semantic Scholar API key.
"""

import argparse
import http.client
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

SOCIALS_FILE = "_data/socials.yml"
BIB_FILE = "_bibliography/papers.bib"
API_URL = "https://api.semanticscholar.org/graph/v1"
ARXIV_API = "https://export.arxiv.org/api/query?id_list="
ATOM = "{http://www.w3.org/2005/Atom}"
USER_AGENT = "al-folio bin/update_publications.py"
FIELDS = "paperId,externalIds,title,year,venue,publicationVenue,journal,authors,abstract,publicationTypes,openAccessPdf"
STOPWORDS = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "toward", "towards", "using", "via", "with"}
VIDEO_URL = re.compile(
    r"https?://(?:(?:www\.|m\.)?youtube(?:-nocookie)?\.com/(?:watch\?(?:[^\"'\s<>]*?[&;])?v=|embed/|shorts/|live/|v/)|youtu\.be/)([\w-]{11})"
    # Vimeo, keeping the hash that unlisted videos need: vimeo.com/<id>/<hash> or player.vimeo.com/video/<id>?h=<hash>
    r"|https?://(?:www\.|player\.)?vimeo\.com/(?:video/)?(\d+)(?:/([0-9a-f]{6,})(?!\w)|\?(?:[^\"'\s<>]*?[&;])?h=([0-9a-f]{6,})(?!\w))?"
)


def load_author_id() -> str:
    """Read `semanticscholar_id` from _data/socials.yml (a flat key: value file)."""
    with open(SOCIALS_FILE) as f:
        match = re.search(r"^semanticscholar_id:\s*[\"']?([^\s\"'#]+)", f.read(), re.MULTILINE)
    if not match:
        sys.exit(f"No 'semanticscholar_id' found in {SOCIALS_FILE}. Add your Semantic Scholar author ID there.")
    return match.group(1)


def get_json(url: str) -> dict:
    """GET a Semantic Scholar API URL, retrying while the API is rate limited or unavailable."""
    headers = {"User-Agent": USER_AGENT}
    if os.environ.get("S2_API_KEY"):
        headers["x-api-key"] = os.environ["S2_API_KEY"]
    request = urllib.request.Request(url, headers=headers)
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            if (e.code != 429 and e.code < 500) or attempt == 5:
                raise
            wait = 5 * 2**attempt
            print(f"Semantic Scholar returned HTTP {e.code}; retrying in {wait}s.")
            time.sleep(wait)


def fetch_papers(author_id: str) -> list:
    """Fetch all papers of an author, following pagination."""
    papers, offset = [], 0
    while offset is not None:
        page = get_json(f"{API_URL}/author/{author_id}/papers?fields={FIELDS}&limit=100&offset={offset}")
        papers += page.get("data", [])
        offset = page.get("next")
    return papers


def videos_in(text: str) -> list:
    """YouTube and Vimeo links in text as canonical URLs, most-linked first (ties keep their order)."""
    found = []
    for youtube, vimeo, path_hash, query_hash in VIDEO_URL.findall(text):
        unlisted = path_hash or query_hash
        found.append(f"https://youtu.be/{youtube}" if youtube else f"https://vimeo.com/{vimeo}" + (f"/{unlisted}" if unlisted else ""))
    return sorted(dict.fromkeys(found), key=found.count, reverse=True)


def bibtex_url(url: str) -> str:
    """Write a URL so the site's LaTeX filter keeps it intact: it turns '--' and '---' into dashes."""
    return re.sub(r"-(?=-)", "-{}", url)


def arxiv_get(url: str) -> str | None:
    """GET an arXiv URL after the 3-second pause arXiv asks of automated clients; None if unavailable.

    When arXiv rate limits (HTTP 429 or 503), waits and tries twice more.
    """
    for attempt in range(3):
        time.sleep(3 if attempt == 0 else 30 * attempt)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=30) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503):
                return None
        except (OSError, http.client.HTTPException, ValueError):  # network errors and timeouts are OSErrors
            return None
    return None


def arxiv_video_versions(arxiv_id: str) -> list:
    """The videos each version of an arXiv paper links, oldest version first.

    A version's videos come from its HTML full text without the reference list, so videos it cites don't
    count; the newest version also counts its abstract and comments. A version whose text
    could not be read is None. The list is empty when the paper's versions could not be looked up.
    """
    feed = arxiv_get(ARXIV_API + urllib.parse.quote(arxiv_id))
    try:
        entry = ET.fromstring(feed).find(ATOM + "entry") if feed else None
    except ET.ParseError:
        entry = None
    newest = re.search(r"v(\d+)$", entry.findtext(ATOM + "id") or "") if entry is not None else None
    if not newest:
        return []
    texts = []
    for version in range(1, int(newest[1]) + 1):
        html = arxiv_get(f"https://arxiv.org/html/{arxiv_id}v{version}")
        texts.append(None if html is None else re.sub(r'<section[^>]*class="ltx_bibliography".*?</section>', "", html, flags=re.S))
    about = (entry.findtext(ATOM + "summary") or "") + " " + (entry.findtext("{http://arxiv.org/schemas/atom}comment") or "")
    if texts[-1] is not None or videos_in(about):
        texts[-1] = (texts[-1] or "") + about
    return [None if text is None else videos_in(text) for text in texts]


def newest_video(versions: list) -> str | None:
    """The video the newest version links ('' if it links none), or None if that version could not be read."""
    if not versions or versions[-1] is None:
        return None
    return versions[-1][0] if versions[-1] else ""


def to_ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def normalize_title(title: str) -> str:
    """Lowercase letters and digits only, so case, accents, LaTeX and punctuation don't matter."""
    return re.sub(r"[^a-z0-9]", "", to_ascii(re.sub(r"\\[a-zA-Z]+", "", title)).lower())


def normalize_arxiv(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", re.sub(r"(?i)^arxiv:", "", arxiv_id.strip()))


def identifiers(s2_id: str = "", doi: str = "", arxiv: str = "", title: str = "") -> set:
    """Identifiers used to recognise the same paper in papers.bib and on Semantic Scholar."""
    ids = set()
    if s2_id:
        ids.add("s2:" + s2_id)
    if doi:
        ids.add("doi:" + doi.lower())
        if doi.lower().startswith("10.48550/arxiv."):
            arxiv = arxiv or doi[len("10.48550/arxiv.") :]
    if arxiv:
        ids.add("arxiv:" + normalize_arxiv(arxiv))
    if normalize_title(title):
        ids.add("title:" + normalize_title(title))
    return ids


def read_value(text: str, start: int) -> str:
    """Return the BibTeX value starting at text[start] (braced or quoted), without its delimiters.

    Braces nest, and a quoted value ends only at a '"' outside braces, as in "M{\\"o}bius".
    """
    depth = 0
    for end in range(start + 1, len(text)):
        char = text[end]
        if char == "{":
            depth += 1
        elif char == "}":
            if depth == 0 and text[start] == "{":
                return text[start + 1 : end]
            depth -= 1
        elif char == '"' and depth == 0 and text[start] == '"':
            return text[start + 1 : end]
    return text[start + 1 :]


def find_field(entry: str, name: str) -> re.Match | None:
    """Locate '<name> =' in one BibTeX entry, at the start of a line or after a comma."""
    return re.search(r"(?:^|,)\s*" + name + r"\s*=\s*", entry, re.MULTILINE | re.IGNORECASE)


def field_value(entry: str, name: str) -> str:
    """The braced or quoted value of a field in one BibTeX entry; '' if it has none or a bare value."""
    match = find_field(entry, name)
    if not match or entry[match.end() : match.end() + 1] not in ("{", '"'):
        return ""
    return read_value(entry, match.end()).strip()


def set_field(entry: str, name: str, value: str) -> str:
    """Set a field of one BibTeX entry to {value}, replacing its value or adding it before the closing brace."""
    match = find_field(entry, name)
    if match and entry[match.end() : match.end() + 1] in ("{", '"'):
        end = match.end() + len(read_value(entry, match.end())) + 2  # past the closing brace or quote
        return entry[: match.end()] + "{" + value + "}" + entry[end:]
    opening = entry.index("{")
    closing = opening + len(read_value(entry, opening)) + 1  # the brace closing the entry, even mid-line
    layout = re.search(r"(?m)^([ \t]+)(\w+[ \t]*)=", entry)  # copy the indentation and alignment of the first field
    indent, key = layout.groups() if layout else ("  ", name + " ")
    head = entry[:closing].rstrip()
    return head + ("" if head.endswith(",") else ",") + f"\n{indent}{name.ljust(len(key))}= {{{value}}}\n" + entry[closing:]


def with_video(entry: str, url: str) -> str:
    """Set an entry's video and mark it `video_auto` so the job keeps it at the newest version's video."""
    return set_field(set_field(entry, "video", bibtex_url(url)), "video_auto", "true")


def update_videos(bib: str) -> tuple:
    """Point each arXiv paper's video at the one its newest version links. Returns (new bib, summary lines).

    A video is added when an entry has none, and replaced when it is marked `video_auto` or an older
    version of the paper linked it; any other video was chosen by hand and stays.
    """
    entries, changes = re.split(r"(?m)^(?=@)", bib), []
    for i, entry in enumerate(entries):
        arxiv_id = normalize_arxiv(field_value(entry, "arxiv")) if entry.startswith("@") else ""
        video_field = find_field(entry, "video")
        if not arxiv_id or (video_field and entry[video_field.end() : video_field.end() + 1] not in ("{", '"')):
            continue  # not on arXiv, or a video given as a macro or bare word, which only a person writes
        versions = arxiv_video_versions(arxiv_id)
        newest = newest_video(versions)
        video = field_value(entry, "video").replace("{}", "")  # undo bibtex_url
        current = (videos_in(video) or [""])[0]
        older = {link for links in versions[:-1] if links for link in links}
        follows_paper = field_value(entry, "video_auto").lower() == "true" or current in older
        if newest and newest != current and (not video or follows_paper):
            entries[i] = with_video(entry, newest)
            title = " ".join(field_value(entry, "title").split())
            change = f"replaced {video} with {newest}" if video else f"added {newest}"
            changes.append(f"- {title}: video {change} (linked by arXiv:{arxiv_id}v{len(versions)})")
            print(f"Video for {title}: {change}")
    return "".join(entries), changes


def existing_entries(bib: str) -> tuple:
    """Return the entry keys and the paper identifiers already present in papers.bib."""
    keys = set(re.findall(r"^@\w+\s*\{\s*([^,\s]+)\s*,", bib, re.MULTILINE))
    known = set()
    for match in re.finditer(r"\b(s2_id|doi|arxiv|eprint|title)\s*=\s*(?=[{\"])", bib, re.IGNORECASE):
        field, value = match.group(1).lower(), read_value(bib, match.end())
        if field == "s2_id":
            known |= identifiers(s2_id=value.strip())
        elif field == "doi":
            known |= identifiers(doi=value.strip())
        elif field == "title":
            known |= identifiers(title=value)
        else:
            known |= identifiers(arxiv=value)
    return keys, known


def paper_identifiers(paper: dict) -> set:
    ext = paper.get("externalIds") or {}
    return identifiers(paper["paperId"], ext.get("DOI") or "", ext.get("ArXiv") or "", paper.get("title") or "")


def bibtex_text(text: str) -> str:
    """Make plain text safe inside a braced BibTeX value."""
    text = re.sub(r"(?<!\\)([&%#])", r"\\\1", " ".join(text.split()))
    depth = 0
    for char in text:
        depth += {"{": 1, "}": -1}.get(char, 0)
        if depth < 0:
            break
    if depth != 0:
        text = text.replace("{", "").replace("}", "")
    return text


def venue_abbr(venue: dict) -> str:
    """Pick an acronym such as IROS or CoRL from the venue's alternate names."""
    names = [name for name in venue.get("alternate_names") or [] if re.fullmatch(r"[A-Z][A-Za-z0-9]{1,7}", name)]
    names = [name for name in names if sum(char.isupper() for char in name) >= 2]
    return min(names, key=len) if names else ""


def make_key(paper: dict, taken: set) -> str:
    """Build a key like papatheodorou2025learning that is not in `taken`."""
    names = ((paper.get("authors") or [{}])[0].get("name") or "").split()
    last_name = re.sub(r"[^a-z]", "", to_ascii(names[-1]).lower()) if names else ""
    words = [word for word in re.findall(r"[a-z0-9]+", to_ascii(paper["title"]).lower()) if word not in STOPWORDS]
    base = f"{last_name}{paper.get('year') or ''}{words[0] if words else ''}" or "paper"
    key, suffix = base, "a"
    while key in taken:
        key, suffix = base + suffix, chr(ord(suffix) + 1)
    taken.add(key)
    return key


def make_entry(paper: dict, key: str, video: str = "") -> str:
    """Format a Semantic Scholar paper as a BibTeX entry for al-folio."""
    ext = paper.get("externalIds") or {}
    venue = paper.get("publicationVenue") or {}
    journal = paper.get("journal") or {}
    if (journal.get("name") or "").lower() in ("arxiv", "arxiv.org"):
        journal = {}  # the arXiv listing, not where the paper was published
    # `journal` holds the published title when known, e.g. "2024 IEEE/RSJ International Conference on ... (IROS)"
    venue_name = journal.get("name") or venue.get("name") or paper.get("venue") or ""
    year = str(paper.get("year") or "")
    arxiv = normalize_arxiv(ext["ArXiv"]) if ext.get("ArXiv") else ""
    doi = ext.get("DOI") or ""
    if doi.lower().startswith("10.48550/arxiv."):
        doi = ""  # arXiv's own DOI; the arXiv button already links there

    fields = {}
    if arxiv and venue_name.lower() in ("", "arxiv", "arxiv.org"):
        entry_type, venue_field, venue_name = "article", "journal", f"arXiv preprint arXiv:{arxiv}"
        fields["abbr"] = "arXiv"
    else:
        is_conference = venue.get("type") == "conference" or "Conference" in (paper.get("publicationTypes") or [])
        if venue_name and is_conference:
            entry_type, venue_field = "inproceedings", "booktitle"
        elif venue_name:
            entry_type, venue_field = "article", "journal"
        else:
            entry_type, venue_field = "misc", ""
        # Semantic Scholar's year is the first (often preprint) version. The published year is the leading year of
        # proceedings titles ("2024 IEEE/RSJ ...") or the volume of journals numbered by year (TMLR: "2026"). Only
        # accept it shortly after the first version, so a book-series volume such as LNCS 2026 is not read as a year.
        published = re.findall(r"^(\d{4})\s", venue_name) + re.findall(r"^(\d{4})$", journal.get("volume") or "")
        if published and (not year or 0 <= int(published[0]) - int(year) <= 3):
            year = published[0]
    if "abbr" not in fields and venue_abbr(venue):
        fields["abbr"] = venue_abbr(venue)
    fields["bibtex_show"] = "true"
    fields["title"] = bibtex_text(paper["title"])
    fields["author"] = " and ".join(bibtex_text(author["name"]) for author in paper.get("authors") or [])
    if venue_field:
        fields[venue_field] = bibtex_text(venue_name)
    if entry_type == "article" and journal.get("volume") and journal["volume"] != year:
        fields["volume"] = bibtex_text(journal["volume"])
    if entry_type in ("article", "inproceedings") and journal.get("pages"):
        fields["pages"] = bibtex_text(journal["pages"]).replace("-", "--").replace("----", "--")
    if year:
        fields["year"] = year
    if doi:
        fields["doi"] = doi
    if arxiv:
        fields["arxiv"] = arxiv
    if (paper.get("openAccessPdf") or {}).get("url"):
        fields["pdf"] = bibtex_url(paper["openAccessPdf"]["url"])
    elif arxiv:
        fields["pdf"] = f"https://arxiv.org/pdf/{arxiv}"
    if video:
        fields["video"] = bibtex_url(video)
        fields["video_auto"] = "true"
    if paper.get("abstract"):
        fields["abstract"] = bibtex_text(paper["abstract"])
    fields["s2_id"] = paper["paperId"]

    width = max(len(name) for name in fields)
    body = ",\n".join(f"  {name.ljust(width)} = {{{value}}}" for name, value in fields.items())
    return f"@{entry_type}{{{key},\n{body}\n}}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Add new papers from Semantic Scholar to " + BIB_FILE)
    parser.add_argument("--summary", help="also write a Markdown list of the added papers to this file")
    args = parser.parse_args()

    author_id = load_author_id()
    print(f"Fetching papers of Semantic Scholar author {author_id}")
    papers = [paper for paper in fetch_papers(author_id) if paper.get("title")]
    print(f"Semantic Scholar lists {len(papers)} papers.")

    bib = ""
    if os.path.exists(BIB_FILE):
        with open(BIB_FILE) as f:
            bib = f.read()
    keys, known = existing_entries(bib)
    updated_bib, video_changes = update_videos(bib)

    new_papers = []
    for paper in sorted(papers, key=lambda paper: paper.get("year") or 0, reverse=True):
        ids = paper_identifiers(paper)
        if not ids & known:
            new_papers.append(paper)
            known |= ids

    entries = []
    for paper in new_papers:
        arxiv_id = normalize_arxiv((paper.get("externalIds") or {}).get("ArXiv") or "")
        video = newest_video(arxiv_video_versions(arxiv_id)) if arxiv_id else None
        if video is None:  # not on arXiv, or its newest version could not be read: use the abstract's link
            video = (videos_in(paper.get("abstract") or "") or [""])[0]
        entries.append(make_entry(paper, make_key(paper, keys), video))
    if not entries and not video_changes:
        print("No new papers and no video changes.")
        return

    if entries:  # newest papers go first
        updated_bib = "\n\n".join(entries) + "\n" + ("\n" + updated_bib.lstrip("\n") if updated_bib.strip() else "")
    with open(BIB_FILE, "w") as f:
        f.write(updated_bib)

    lines = []
    if new_papers:
        lines += ["Semantic Scholar lists these papers that are not on the site yet:", ""]
        for paper in new_papers:
            venue = paper.get("venue") or "no venue"
            print(f"Added: {paper['title']} ({venue}, {paper.get('year')})")
            lines.append(f"- [{paper['title']}](https://www.semanticscholar.org/paper/{paper['paperId']}) ({venue}, {paper.get('year')})")
        lines.append("")
    if video_changes:
        lines += ["These papers' newest arXiv versions link a different video than the site shows:", "", *video_changes, ""]
    lines += [
        f"Check the entries in `{BIB_FILE}`, edit them on this branch if needed, then merge to publish.",
        "If a paper is not yours, remove it from your Semantic Scholar author page, or it will be suggested again.",
    ]
    if args.summary:
        with open(args.summary, "w") as f:
            f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
