"""
Targeted Automated Literature Fetcher for the AI_CAO knowledge base.

Searches two TRUSTED, free scholarly APIs for open-access papers, downloads the
PDFs, and ingests them (chunk -> embed -> Pinecone) tagged as public literature.
It is NOT a web scraper: it never visits arbitrary sites.

Run from the backend folder:
    venv\\Scripts\\python.exe scripts\\auto_fetch_literature.py --dry-run
    venv\\Scripts\\python.exe scripts\\auto_fetch_literature.py --limit 3

Sources
  crossref  Crossref REST API: journal articles with a Creative Commons licence.
  pubmed    NCBI E-utilities over PubMed Central (PMC, PubMed's full-text archive),
            PDFs from NCBI's open-data bucket (pmc-oa-opendata on AWS S3).

GUARDRAILS (do not weaken)
  * OPEN ACCESS ONLY: a paper is used only if its licence is CC BY, CC BY-SA or
    CC0/public domain, which permit commercial reuse. Publisher text-mining
    licences (Elsevier, Springer "tdm"), NonCommercial (NC), NoDerivatives (ND)
    and unknown licences are SKIPPED. Anything that does not download as a real
    PDF is skipped.
  * STRICT TAGGING: every chunk gets document_type "auto_sourced_literature",
    source_api ("crossref" or "pubmed"), paper_title and year, so the AI_CAO can
    tell public literature from Century Aqua's own data. Papers missing a title
    or year are skipped, never ingested untagged.
  * COST CONTROL: --limit (default 3, hard maximum 20) and a y/n confirmation
    listing every paper before anything is downloaded or embedded.
  * Already-ingested papers are remembered in temp_literature/manifest.json and
    skipped on later runs.

Only the standard library is used (no new dependencies).
Exit codes: 0 ok (including "nothing found" or "declined"), 1 a paper failed or
setup is incomplete, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from app.rag import embeddings, vector_db  # noqa: E402
from app.rag.document_processor import AUTO_SOURCED_TYPE  # noqa: E402
from app.rag.ingestion import ingest_file  # noqa: E402

TEMP_DIR = BACKEND_DIR / "temp_literature"
MANIFEST_PATH = TEMP_DIR / "manifest.json"

SEARCH_QUERIES: tuple[str, ...] = (
    "biofloc tilapia water quality",
    "simultaneous nitrification denitrification aquaculture",
    "recirculating aquaculture system ammonia control",
    "tilapia seawater culture salinity tolerance",
    "probiotics Bacillus water quality aquaculture ammonia nitrite",
)

DEFAULT_LIMIT = 3
MAX_LIMIT = 20
CANDIDATES_PER_SEARCH = 25
MAX_RESOLVE_ATTEMPTS = 40
MAX_PDF_BYTES = 40 * 1024 * 1024
NCBI_DELAY = 0.4  # seconds between NCBI calls (limit: 3 per second without a key)

CROSSREF_URL = "https://api.crossref.org/works"
EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PMC_S3_URL = "https://pmc-oa-opendata.s3.amazonaws.com"
USER_AGENT = "CenturyAquaAI-literature-fetcher/1.0"

# Licences that allow commercial reuse and copying. NC and ND are deliberately absent.
_CC_URL = re.compile(r"creativecommons\.org/licenses/(by|by-sa)/\d", re.IGNORECASE)
_CC0_URL = re.compile(r"creativecommons\.org/publicdomain/(zero|mark)/", re.IGNORECASE)
_PMC_CODE = re.compile(r"^(CC0.*|CC BY(-SA)?([ \-]?\d.*)?)$", re.IGNORECASE)


class FetchError(Exception):
    """A network or download problem for one request (message has no URL or secrets)."""


@dataclass(frozen=True)
class Paper:
    """One candidate paper. ``verified`` papers have a confirmed licence and PDF URL."""

    source_api: str  # "crossref" or "pubmed"
    title: str
    year: int
    doi: str = ""
    pmcid: str = ""
    pdf_url: str = ""
    license: str = ""
    query: str = ""
    verified: bool = False

    @property
    def paper_id(self) -> str:
        return self.doi or self.pmcid

    def keys(self) -> set[str]:
        """Identity keys used to skip duplicates across sources and across runs."""
        found = {"title:" + normalize_title(self.title)}
        if self.doi:
            found.add("doi:" + self.doi.lower())
        if self.pmcid:
            found.add("pmcid:" + self.pmcid.upper())
        return found


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def normalize_title(title: str) -> str:
    """Lower-case letters and digits only, for duplicate detection."""
    return re.sub(r"[^a-z0-9]+", " ", html.unescape(title).lower()).strip()


def clean_title(title: str) -> str:
    """Strip markup (JATS/HTML tags) and collapse whitespace."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", title))).strip()


def is_open_license(value: str) -> bool:
    """True only for CC BY, CC BY-SA and CC0 / public domain (commercial reuse allowed)."""
    value = (value or "").strip()
    if not value:
        return False
    if _CC_URL.search(value) or _CC0_URL.search(value):
        return True
    return bool(_PMC_CODE.match(value))


def is_safe_url(url: str) -> bool:
    """HTTPS only, and the host must resolve only to public internet addresses."""
    import socket

    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or not host or host.lower() == "localhost":
        return False
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, parts.port or 443)}
    except OSError:
        return False
    try:
        return bool(addresses) and all(ipaddress.ip_address(a).is_global for a in addresses)
    except ValueError:
        return False


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Re-check every redirect target, so a link cannot bounce us to an internal host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not is_safe_url(newurl):
            raise FetchError("redirected to an unsafe address")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(
    url: str,
    params: dict[str, str] | None = None,
    *,
    timeout: float = 30.0,
    retries: int = 2,
    max_bytes: int = 5 * 1024 * 1024,
) -> bytes:
    """GET with retries on rate limits and server errors. Raises FetchError."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    if not is_safe_url(url):
        raise FetchError("refusing to fetch a non-HTTPS or non-public address")
    agent = USER_AGENT
    if os.getenv("CONTACT_EMAIL"):
        agent += f" (mailto:{os.environ['CONTACT_EMAIL']})"  # Crossref's "polite pool"
    opener = urllib.request.build_opener(_SafeRedirect)
    last = "unknown error"
    for attempt in range(retries + 1):
        retryable = True
        try:
            request = urllib.request.Request(url, headers={"User-Agent": agent})
            with opener.open(request, timeout=timeout) as response:
                data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise FetchError("response too large")
            return data
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            retryable = exc.code in (429, 500, 502, 503, 504)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = type(exc).__name__
        if not retryable or attempt == retries:
            break
        time.sleep(1.5 * (attempt + 1))
    raise FetchError(last)


def get_json(url: str, params: dict[str, str] | None = None) -> Any:
    try:
        return json.loads(http_get(url, params).decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise FetchError("invalid JSON in response") from exc



# --------------------------------------------------------------------------- #
# Crossref
# --------------------------------------------------------------------------- #
def _crossref_year(item: dict[str, Any]) -> int | None:
    for field in ("issued", "published", "published-print", "published-online"):
        try:
            year = item[field]["date-parts"][0][0]
        except (KeyError, IndexError, TypeError):
            continue
        if isinstance(year, int) and 1800 <= year <= datetime.now(timezone.utc).year + 1:
            return year
    return None


def parse_crossref_item(item: dict[str, Any], query: str = "") -> Paper | None:
    """Turn one Crossref record into a verified Paper, or None if it fails any guardrail."""
    doi = str(item.get("DOI") or "").strip()
    titles = item.get("title") or []
    title = clean_title(titles[0]) if titles else ""
    year = _crossref_year(item)
    if not (doi and title and year):
        return None  # strict tagging: never ingest without title and year

    open_licenses = [
        lic for lic in item.get("license") or []
        if is_open_license(str(lic.get("URL", ""))) and int(lic.get("delay-in-days") or 0) <= 0
    ]
    if not open_licenses:
        return None  # not explicitly open access with a reuse-friendly licence

    pdf_url = ""
    for link in item.get("link") or []:
        url = str(link.get("URL", ""))
        path = urllib.parse.urlsplit(url).path.lower()
        is_pdf = link.get("content-type") == "application/pdf" or path.endswith(".pdf")
        if url.startswith("https://") and is_pdf:
            pdf_url = url
            break
    if not pdf_url:
        return None
    return Paper("crossref", title, year, doi=doi, pdf_url=pdf_url,
                 license=str(open_licenses[0]["URL"]), query=query, verified=True)


def search_crossref(query: str, rows: int = CANDIDATES_PER_SEARCH) -> list[Paper]:
    """Crossref search for journal articles that carry a licence and a full-text link."""
    data = get_json(CROSSREF_URL, {
        "query.bibliographic": query,
        "filter": "type:journal-article,has-license:true,has-full-text:true",
        "rows": str(rows),
        "select": "DOI,title,issued,published,license,link",
    })
    items = (data.get("message") or {}).get("items") or []
    return [p for p in (parse_crossref_item(i, query) for i in items) if p]



# --------------------------------------------------------------------------- #
# PubMed Central (via NCBI E-utilities)
# --------------------------------------------------------------------------- #
def _ncbi_params(extra: dict[str, str]) -> dict[str, str]:
    params = {"tool": "century_aqua_ai", "retmode": "json", **extra}
    if os.getenv("CONTACT_EMAIL"):
        params["email"] = os.environ["CONTACT_EMAIL"]
    return params


def parse_pubmed_summary(record: dict[str, Any], query: str = "") -> Paper | None:
    """Turn one esummary record into an UNVERIFIED candidate (licence still to be checked)."""
    title = clean_title(str(record.get("title") or ""))
    ids = {a.get("idtype"): str(a.get("value", "")) for a in record.get("articleids") or []}
    pmcid = ids.get("pmcid", "").upper()
    doi = ids.get("doi", "")
    match = re.search(r"\b(1[89]\d\d|20\d\d)\b", str(record.get("pubdate") or record.get("epubdate") or ""))
    year = int(match.group(1)) if match else None
    if not (title and pmcid.startswith("PMC") and year):
        return None
    return Paper("pubmed", title, year, doi=doi, pmcid=pmcid, query=query)


def search_pubmed(query: str, rows: int = CANDIDATES_PER_SEARCH) -> list[Paper]:
    """PMC search restricted to NCBI's "open access" subset. Licences are verified later."""
    found = get_json(f"{EUTILS_URL}/esearch.fcgi", _ncbi_params({
        "db": "pmc",
        "term": f'({query}) AND "open access"[filter]',
        "retmax": str(rows),
        "sort": "relevance",
    }))
    ids = (found.get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return []
    time.sleep(NCBI_DELAY)
    summary = get_json(f"{EUTILS_URL}/esummary.fcgi", _ncbi_params({"db": "pmc", "id": ",".join(ids)}))
    result = summary.get("result") or {}
    records = (result.get(uid) for uid in result.get("uids") or [])
    return [p for p in (parse_pubmed_summary(r, query) for r in records if r) if p]


def verify_pubmed_paper(paper: Paper) -> Paper | None:
    """
    Confirm, from NCBI's own open-data record, that the paper is in the open-access
    set with a CC BY / CC BY-SA / CC0 licence and has a PDF. Returns a verified
    copy, or None to skip the paper.
    """
    base = f"{PMC_S3_URL}/{paper.pmcid}.1/{paper.pmcid}.1"
    try:
        meta = json.loads(http_get(f"{base}.json", retries=1).decode("utf-8", errors="replace"))
    except (FetchError, ValueError):
        return None  # no record, or not in the open-data set: skip
    if not meta.get("is_pmc_openaccess") or meta.get("is_retracted"):
        return None
    if not is_open_license(str(meta.get("license_code") or "")):
        return None  # NC, ND, unknown or missing licence
    if not meta.get("pdf_url"):
        return None
    return replace(
        paper, pdf_url=f"{base}.pdf", license=str(meta["license_code"]),
        doi=paper.doi or str(meta.get("doi") or ""), verified=True,
    )



# --------------------------------------------------------------------------- #
# Manifest, selection and download
# --------------------------------------------------------------------------- #
def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """Papers already ingested by earlier runs ({"papers": [{keys, title, ...}]})."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data.get("papers"), list):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {"papers": []}


def manifest_keys(manifest: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for entry in manifest.get("papers", []):
        keys.update(entry.get("keys", []))
    return keys


def record_in_manifest(manifest: dict[str, Any], paper: Paper, chunks: int, path: Path = MANIFEST_PATH) -> None:
    manifest["papers"].append({
        "keys": sorted(paper.keys()), "title": paper.title, "year": paper.year,
        "source_api": paper.source_api, "license": paper.license, "chunks": chunks,
        "ingested": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def select_papers(
    queries: tuple[str, ...] | list[str],
    limit: int,
    sources: tuple[str, ...],
    known: set[str],
    log: Callable[[str], None] = print,
    *,
    crossref_search: Callable[[str], list[Paper]] = search_crossref,
    pubmed_search: Callable[[str], list[Paper]] = search_pubmed,
    pubmed_verify: Callable[[Paper], Paper | None] = verify_pubmed_paper,
) -> list[Paper]:
    """
    Pick up to ``limit`` verified open-access papers, round-robin across queries so
    one topic cannot use up the whole budget. Duplicates (within this run, across
    sources, and against the manifest) are skipped. Stops searching once enough
    papers are found, and caps PubMed licence checks at MAX_RESOLVE_ATTEMPTS.
    """
    pools: list[list[Paper]] = []
    for query in queries:
        pool: list[Paper] = []
        for source, search in (("crossref", crossref_search), ("pubmed", pubmed_search)):
            if source not in sources:
                continue
            try:
                pool.extend(search(query))
            except FetchError as exc:
                log(f"  ! {source} search failed for '{query}': {exc}")
            if source == "pubmed":
                time.sleep(NCBI_DELAY)
        pools.append(pool)

    chosen: list[Paper] = []
    seen = set(known)
    attempts = 0
    while len(chosen) < limit and any(pools):
        for pool in pools:
            while pool and len(chosen) < limit:
                paper = pool.pop(0)
                if paper.keys() & seen:
                    continue
                if not paper.verified:
                    if attempts >= MAX_RESOLVE_ATTEMPTS:
                        continue
                    attempts += 1
                    time.sleep(NCBI_DELAY)
                    paper = pubmed_verify(paper)  # type: ignore[assignment]
                    if paper is None:
                        continue
                chosen.append(paper)
                seen |= paper.keys()
                break  # one paper per query per round
            if len(chosen) >= limit:
                break
    return chosen


def download_pdf(paper: Paper, folder: Path = TEMP_DIR) -> Path:
    """Download the PDF into ``folder``. Raises FetchError unless it is a real PDF."""
    data = http_get(paper.pdf_url, timeout=90.0, max_bytes=MAX_PDF_BYTES)
    if not data.startswith(b"%PDF-"):
        raise FetchError("not a PDF (the link returned a web page)")
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", normalize_title(paper.title))[:50].strip("-") or "paper"
    ident = re.sub(r"[^A-Za-z0-9]+", "-", paper.paper_id)[:40].strip("-")
    path = folder / f"{paper.source_api}_{ident}_{slug}.pdf"
    path.write_bytes(data)
    return path



# --------------------------------------------------------------------------- #
# Ingest, CLI and main
# --------------------------------------------------------------------------- #
def tagging_metadata(paper: Paper) -> dict[str, Any]:
    """The strict tags stored on every chunk (document_type is added by ingestion)."""
    return {
        "source_api": paper.source_api,
        "paper_title": paper.title,
        "year": paper.year,
        "doi": paper.doi,
        "license": paper.license,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch open-access literature (Crossref / PubMed Central) into the AI_CAO knowledge base."
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"Maximum papers per run (default {DEFAULT_LIMIT}, hard maximum {MAX_LIMIT}).")
    parser.add_argument("--source", choices=("crossref", "pubmed", "both"), default="both",
                        help="Which API to search (default both).")
    parser.add_argument("--query", action="append", default=None, metavar="TEXT",
                        help="Search text; repeat for several. Default: the built-in aquaculture list.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Search and list the papers only. No downloads, no embeddings, no keys needed.")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the y/n question (still limited by --limit).")
    return parser


def _confirm(prompt: str, input_fn: Callable[[str], str]) -> bool:
    try:
        return input_fn(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


async def ingest_papers(
    papers: list[Paper], manifest: dict[str, Any], log: Callable[[str], None],
    *, download: Callable[[Paper], Path] = download_pdf, ingest: Any = ingest_file,
) -> tuple[int, list[str]]:
    """Download and ingest each paper. A failure is reported and the rest continue."""
    total_chunks = 0
    failures: list[str] = []
    for number, paper in enumerate(papers, start=1):
        log(f"[{number}/{len(papers)}] {paper.title[:90]} ({paper.year}, {paper.source_api})")
        path: Path | None = None
        try:
            log("  downloading PDF ...")
            path = download(paper)
            result = await ingest(path, AUTO_SOURCED_TYPE, log=log, extra_metadata=tagging_metadata(paper))
            total_chunks += result.chunks
            record_in_manifest(manifest, paper, result.chunks)
        except Exception as exc:  # one bad paper must not stop the run
            failures.append(f"{paper.title[:60]}: {type(exc).__name__}: {exc}")
            log(f"  FAILED: {type(exc).__name__}: {exc}")
        finally:
            if path is not None:
                path.unlink(missing_ok=True)  # keep only the vectors, not the PDF
    return total_chunks, failures


def main(
    argv: list[str] | None = None,
    *,
    input_fn: Callable[[str], str] = input,
    select: Callable[..., list[Paper]] = select_papers,
    ingest_fn: Callable[..., Any] = ingest_papers,
    manifest_path: Path = MANIFEST_PATH,
) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.limit <= MAX_LIMIT:
        print(f"error: --limit must be between 1 and {MAX_LIMIT}.", file=sys.stderr)
        return 2
    sources = ("crossref", "pubmed") if args.source == "both" else (args.source,)
    queries = tuple(args.query) if args.query else SEARCH_QUERIES

    if not args.dry_run:  # fail fast, before any search or download
        problems = []
        if not embeddings.is_configured():
            problems.append("OPENAI_API_KEY")
        if not vector_db.is_configured():
            problems.append("PINECONE_API_KEY and PINECONE_INDEX")
        if problems:
            print(f"error: missing {' and '.join(problems)} in backend/.env (or use --dry-run).", file=sys.stderr)
            return 1

    manifest = load_manifest(manifest_path)
    print(f"Searching {' and '.join(sources)} for open-access papers ({len(queries)} queries, limit {args.limit}) ...")
    papers = select(queries, args.limit, sources, manifest_keys(manifest), print)
    if not papers:
        print("No new open-access papers found.")
        return 0

    print(f"\nFound {len(papers)} open-access paper(s):")
    for number, paper in enumerate(papers, start=1):
        print(f"  {number}. {paper.title[:100]} ({paper.year})")
        print(f"     source: {paper.source_api} | licence: {paper.license} | query: {paper.query}")
    if args.dry_run:
        print("\nDry run: nothing was downloaded or embedded.")
        return 0

    if not args.yes and not _confirm(f"\nFound {len(papers)} open-access papers. Proceed? (y/n) ", input_fn):
        print("Cancelled. Nothing was downloaded or embedded.")
        return 0

    total_chunks, failures = asyncio.run(ingest_fn(papers, manifest, print))
    ok = len(papers) - len(failures)
    print(f"\nSummary: {ok}/{len(papers)} paper(s) ingested, {total_chunks} chunks stored as '{AUTO_SOURCED_TYPE}'.")
    for line in failures:
        print(f"  failed: {line}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

