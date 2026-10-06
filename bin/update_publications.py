#!/usr/bin/env python

"""Add new papers from Semantic Scholar to _bibliography/papers.bib.

Reads the author ID from `semanticscholar_id` in _data/socials.yml, fetches the author's papers
and adds a BibTeX entry for every paper not already in papers.bib (matched by Semantic Scholar
paper ID, DOI, arXiv ID or title). Existing entries are never changed, so hand edits such as
links, thumbnails or `selected` survive every run.

Uses only the standard library. Set S2_API_KEY to send a Semantic Scholar API key.
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request

SOCIALS_FILE = "_data/socials.yml"
BIB_FILE = "_bibliography/papers.bib"
API_URL = "https://api.semanticscholar.org/graph/v1"
FIELDS = "paperId,externalIds,title,year,venue,publicationVenue,journal,authors,abstract,publicationTypes,openAccessPdf"
STOPWORDS = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "toward", "towards", "using", "via", "with"}


def load_author_id() -> str:
    """Read `semanticscholar_id` from _data/socials.yml (a flat key: value file)."""
    with open(SOCIALS_FILE) as f:
        match = re.search(r"^semanticscholar_id:\s*[\"']?([^\s\"'#]+)", f.read(), re.MULTILINE)
    if not match:
        sys.exit(f"No 'semanticscholar_id' found in {SOCIALS_FILE}. Add your Semantic Scholar author ID there.")
    return match.group(1)


def get_json(url: str) -> dict:
    """GET a Semantic Scholar API URL, retrying while the API is rate limited or unavailable."""
    headers = {"User-Agent": "al-folio bin/update_publications.py"}
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
    """Return the BibTeX value starting at text[start] (braced or quoted), without its delimiters."""
    if text[start] == '"':
        return text[start + 1 : text.index('"', start + 1)]
    depth = 0
    for end in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[end], 0)
        if depth == 0:
            return text[start + 1 : end]
    return text[start + 1 :]


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


def make_entry(paper: dict, key: str) -> str:
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
        fields["pdf"] = paper["openAccessPdf"]["url"]
    elif arxiv:
        fields["pdf"] = f"https://arxiv.org/pdf/{arxiv}"
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

    new_papers = []
    for paper in sorted(papers, key=lambda paper: paper.get("year") or 0, reverse=True):
        ids = paper_identifiers(paper)
        if not ids & known:
            new_papers.append(paper)
            known |= ids
    if not new_papers:
        print("No new papers.")
        return

    entries = [make_entry(paper, make_key(paper, keys)) for paper in new_papers]
    with open(BIB_FILE, "w") as f:
        f.write("\n\n".join(entries) + "\n" + ("\n" + bib.lstrip("\n") if bib.strip() else ""))

    lines = ["Semantic Scholar lists these papers that are not on the site yet:", ""]
    for paper in new_papers:
        venue = paper.get("venue") or "no venue"
        print(f"Added: {paper['title']} ({venue}, {paper.get('year')})")
        lines.append(f"- [{paper['title']}](https://www.semanticscholar.org/paper/{paper['paperId']}) ({venue}, {paper.get('year')})")
    lines += [
        "",
        f"Check the authors, venue and year in `{BIB_FILE}`, edit them on this branch if needed, then merge to publish.",
        "If a paper is not yours, remove it from your Semantic Scholar author page, or it will be suggested again.",
    ]
    if args.summary:
        with open(args.summary, "w") as f:
            f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
