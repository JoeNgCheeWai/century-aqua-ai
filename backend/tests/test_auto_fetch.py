"""
Mock tests for scripts/auto_fetch_literature.py and the "auto_sourced_literature"
tagging in app/rag.

NO network calls: every HTTP request, embedding and Pinecone call is replaced by
a fake. Crossref/PubMed payloads below mirror the real response shapes.

Run from the backend folder:
    venv\\Scripts\\python.exe -m unittest tests.test_auto_fetch -v
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
for _p in (str(BACKEND_DIR), str(BACKEND_DIR / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The script calls load_dotenv(backend/.env) on import; keep real keys out of tests.
with mock.patch("dotenv.load_dotenv", lambda *a, **k: False):
    import auto_fetch_literature as afl

from app.rag import document_processor as dp  # noqa: E402
from app.rag import embeddings, retrieval, vector_db  # noqa: E402
from tests.test_rag import make_pdf, sentences  # noqa: E402

CC_BY = "https://creativecommons.org/licenses/by/4.0"


def crossref_item(**over):
    item = {
        "DOI": "10.1000/abc123",
        "title": ["Biofloc <i>tilapia</i> water quality"],
        "issued": {"date-parts": [[2022, 5, 1]]},
        "license": [{"URL": CC_BY, "delay-in-days": 0, "content-version": "vor"}],
        "link": [{"URL": "https://example.org/paper.pdf", "content-type": "application/pdf"}],
    }
    item.update(over)
    return item


def paper(**over) -> afl.Paper:
    base = dict(source_api="crossref", title="A paper", year=2022, doi="10.1/x",
                pdf_url="https://example.org/a.pdf", license=CC_BY, query="q", verified=True)
    base.update(over)
    return afl.Paper(**base)


class LicenseTests(unittest.TestCase):
    def test_commercial_reuse_licences_accepted(self):
        for value in (
            "https://creativecommons.org/licenses/by/4.0",
            "http://creativecommons.org/licenses/by/3.0/",
            "https://creativecommons.org/licenses/by-sa/4.0/legalcode",
            "https://creativecommons.org/publicdomain/zero/1.0/",
            "CC BY", "CC BY 4.0", "CC BY-SA", "CC0", "cc by",
        ):
            self.assertTrue(afl.is_open_license(value), value)

    def test_restricted_or_unknown_licences_rejected(self):
        for value in (
            "", None, "https://creativecommons.org/licenses/by-nc/4.0",
            "https://creativecommons.org/licenses/by-nd/4.0",
            "https://creativecommons.org/licenses/by-nc-sa/4.0",
            "CC BY-NC", "CC BY-ND", "CC BY-NC-ND 4.0", "NO-CC CODE", "other",
            "https://www.elsevier.com/tdm/userlicense/1.0/",
            "https://www.springernature.com/gp/researchers/text-and-data-mining",
            "https://doi.org/10.15223/policy-017",
        ):
            self.assertFalse(afl.is_open_license(value), value)


class CrossrefParsingTests(unittest.TestCase):
    def test_good_item_is_verified_and_cleaned(self):
        p = afl.parse_crossref_item(crossref_item(), "my query")
        self.assertEqual(p.source_api, "crossref")
        self.assertEqual(p.title, "Biofloc tilapia water quality")  # markup stripped
        self.assertEqual(p.year, 2022)
        self.assertEqual(p.doi, "10.1000/abc123")
        self.assertEqual(p.pdf_url, "https://example.org/paper.pdf")
        self.assertEqual(p.license, CC_BY)
        self.assertEqual(p.query, "my query")
        self.assertTrue(p.verified)

    def test_publisher_tdm_licence_rejected(self):
        item = crossref_item(license=[{"URL": "https://www.elsevier.com/tdm/userlicense/1.0/", "delay-in-days": 0}])
        self.assertIsNone(afl.parse_crossref_item(item))

    def test_noncommercial_rejected(self):
        item = crossref_item(license=[{"URL": "https://creativecommons.org/licenses/by-nc/4.0", "delay-in-days": 0}])
        self.assertIsNone(afl.parse_crossref_item(item))

    def test_embargoed_licence_rejected(self):
        item = crossref_item(license=[{"URL": CC_BY, "delay-in-days": 365}])
        self.assertIsNone(afl.parse_crossref_item(item))

    def test_no_pdf_link_rejected(self):
        for links in ([], [{"URL": "https://example.org/page.html", "content-type": "text/html"}],
                      [{"URL": "http://example.org/insecure.pdf", "content-type": "application/pdf"}]):
            self.assertIsNone(afl.parse_crossref_item(crossref_item(link=links)), links)

    def test_pdf_detected_by_extension_when_type_unspecified(self):
        item = crossref_item(link=[{"URL": "https://example.org/p.PDF", "content-type": "unspecified"}])
        self.assertIsNotNone(afl.parse_crossref_item(item))

    def test_missing_title_year_or_doi_rejected(self):
        self.assertIsNone(afl.parse_crossref_item(crossref_item(title=[])))
        self.assertIsNone(afl.parse_crossref_item(crossref_item(issued={})))
        self.assertIsNone(afl.parse_crossref_item(crossref_item(DOI="")))

    def test_year_falls_back_to_published(self):
        item = crossref_item(issued={}, published={"date-parts": [[2019]]})
        self.assertEqual(afl.parse_crossref_item(item).year, 2019)

    def test_search_crossref_builds_the_request_and_filters(self):
        payload = {"message": {"items": [crossref_item(), crossref_item(DOI="10.2/b", license=[])]}}
        with mock.patch.object(afl, "get_json", return_value=payload) as get:
            papers = afl.search_crossref("biofloc tilapia", rows=7)
        self.assertEqual([p.doi for p in papers], ["10.1000/abc123"])
        url, params = get.call_args.args
        self.assertEqual(url, afl.CROSSREF_URL)
        self.assertEqual(params["query.bibliographic"], "biofloc tilapia")
        self.assertEqual(params["rows"], "7")
        self.assertIn("has-license:true", params["filter"])
        self.assertIn("type:journal-article", params["filter"])


SUMMARY_RECORD = {
    "uid": "13739940",
    "pubdate": "2026 Nov 5",
    "title": "Integrative assessment in <i>tambaqui</i>.",
    "articleids": [
        {"idtype": "pmid", "value": "42986529"},
        {"idtype": "doi", "value": "10.1007/s10695-026-01826-6"},
        {"idtype": "pmcid", "value": "PMC13739940"},
    ],
}


class PubmedTests(unittest.TestCase):
    def test_summary_becomes_unverified_candidate(self):
        p = afl.parse_pubmed_summary(SUMMARY_RECORD, "q")
        self.assertEqual((p.source_api, p.year, p.pmcid), ("pubmed", 2026, "PMC13739940"))
        self.assertEqual(p.title, "Integrative assessment in tambaqui.")
        self.assertEqual(p.doi, "10.1007/s10695-026-01826-6")
        self.assertFalse(p.verified)
        self.assertEqual(p.pdf_url, "")

    def test_summary_missing_fields_rejected(self):
        for change in ({"title": ""}, {"pubdate": "", "epubdate": ""}, {"articleids": []}):
            self.assertIsNone(afl.parse_pubmed_summary({**SUMMARY_RECORD, **change}), change)

    def test_search_pubmed_requests_open_access_filter_and_parses(self):
        found = {"esearchresult": {"idlist": ["13739940"]}}
        summary = {"result": {"uids": ["13739940"], "13739940": SUMMARY_RECORD}}
        with mock.patch.object(afl, "get_json", side_effect=[found, summary]) as get, \
             mock.patch.object(afl.time, "sleep"):
            papers = afl.search_pubmed("biofloc", rows=4)
        self.assertEqual([p.pmcid for p in papers], ["PMC13739940"])
        first_params = get.call_args_list[0].args[1]
        self.assertEqual(first_params["db"], "pmc")
        self.assertIn('"open access"[filter]', first_params["term"])
        self.assertEqual(first_params["retmax"], "4")

    def test_search_pubmed_no_results(self):
        with mock.patch.object(afl, "get_json", return_value={"esearchresult": {"idlist": []}}):
            self.assertEqual(afl.search_pubmed("zzz"), [])

    def verify(self, meta, *, fail=False):
        cand = afl.parse_pubmed_summary(SUMMARY_RECORD, "q")
        patch = mock.patch.object(
            afl, "http_get",
            side_effect=afl.FetchError("HTTP 404") if fail else None,
            return_value=json.dumps(meta).encode(),
        )
        with patch as get:
            result = afl.verify_pubmed_paper(cand)
        return result, get

    def test_verified_when_open_access_cc_by_with_pdf(self):
        meta = {"is_pmc_openaccess": True, "license_code": "CC BY", "pdf_url": "s3://x.pdf", "doi": "10.9/z"}
        result, get = self.verify(meta)
        self.assertTrue(result.verified)
        self.assertEqual(result.license, "CC BY")
        self.assertEqual(result.pdf_url, f"{afl.PMC_S3_URL}/PMC13739940.1/PMC13739940.1.pdf")
        self.assertIn("PMC13739940.1.json", get.call_args.args[0])

    def test_skipped_for_restrictive_or_missing_licence_or_retraction(self):
        base = {"is_pmc_openaccess": True, "license_code": "CC BY", "pdf_url": "s3://x.pdf"}
        for change in ({"license_code": "CC BY-NC"}, {"license_code": "CC BY-NC-ND"},
                       {"license_code": "NO-CC CODE"}, {"license_code": None},
                       {"is_pmc_openaccess": False}, {"is_retracted": True}, {"pdf_url": ""}):
            result, _ = self.verify({**base, **change})
            self.assertIsNone(result, change)

    def test_skipped_when_no_open_data_record(self):
        result, _ = self.verify({}, fail=True)
        self.assertIsNone(result)



class SelectionTests(unittest.TestCase):
    def pick(self, queries, limit, crossref, pubmed=lambda q: [], known=None, verify=None,
             sources=("crossref", "pubmed")):
        def default_verify(p):
            return afl.replace(p, verified=True, pdf_url="https://x/a.pdf", license="CC BY")

        with mock.patch.object(afl.time, "sleep"):
            return afl.select_papers(
                queries, limit, sources, known or set(), lambda m: None,
                crossref_search=crossref, pubmed_search=pubmed,
                pubmed_verify=verify or default_verify,
            )

    def test_limit_respected_and_round_robin_across_queries(self):
        def crossref(q):
            return [paper(title=f"{q} paper {i}", doi=f"10.1/{q}{i}") for i in range(10)]

        chosen = self.pick(["a", "b", "c"], 4, crossref)
        self.assertEqual(len(chosen), 4)
        self.assertEqual({p.title.split()[0] for p in chosen}, {"a", "b", "c"})

    def test_duplicates_skipped_across_sources_and_manifest(self):
        shared = paper(title="Same Paper", doi="10.1/same")
        twin = paper(source_api="pubmed", title="Same paper!", doi="", pmcid="PMC1", verified=False)
        other = paper(title="Other", doi="10.1/other")
        chosen = self.pick(["q"], 5, lambda q: [shared, other], lambda q: [twin])
        self.assertEqual(sorted(p.title for p in chosen), ["Other", "Same Paper"])
        chosen = self.pick(["q"], 5, lambda q: [shared, other], known={"doi:10.1/other"})
        self.assertEqual([p.title for p in chosen], ["Same Paper"])

    def test_unverified_pubmed_candidates_checked_and_failures_skipped(self):
        cands = [paper(source_api="pubmed", title=f"P{i}", doi="", pmcid=f"PMC{i}", verified=False)
                 for i in range(5)]
        checked: list[str] = []

        def verify(p):
            checked.append(p.pmcid)
            if p.pmcid in ("PMC0", "PMC1"):
                return None
            return afl.replace(p, verified=True, pdf_url="https://x/a.pdf", license="CC BY")

        chosen = self.pick(["q"], 2, lambda q: [], lambda q: cands, verify=verify)
        self.assertEqual([p.pmcid for p in chosen], ["PMC2", "PMC3"])
        self.assertEqual(checked, ["PMC0", "PMC1", "PMC2", "PMC3"])  # PMC4 never checked

    def test_licence_check_budget_is_capped(self):
        cands = [paper(source_api="pubmed", title=f"P{i}", doi="", pmcid=f"PMC{i}", verified=False)
                 for i in range(100)]
        calls: list = []
        chosen = self.pick(["q"], 3, lambda q: [], lambda q: cands,
                           verify=lambda p: calls.append(p) or None)
        self.assertEqual(chosen, [])
        self.assertEqual(len(calls), afl.MAX_RESOLVE_ATTEMPTS)

    def test_search_failure_is_reported_and_source_filter_applies(self):
        def boom(q):
            raise afl.FetchError("HTTP 503")

        messages: list[str] = []
        with mock.patch.object(afl.time, "sleep"):
            chosen = afl.select_papers(
                ["q"], 3, ("crossref", "pubmed"), set(), messages.append,
                crossref_search=boom, pubmed_search=lambda q: [paper(title="Survivor")],
            )
        self.assertEqual([p.title for p in chosen], ["Survivor"])
        self.assertTrue(any("crossref search failed" in m and "503" in m for m in messages))
        self.assertEqual(self.pick(["q"], 3, lambda q: [paper(title="CR")], sources=("pubmed",)), [])


class SafetyAndDownloadTests(unittest.TestCase):
    def test_unsafe_urls_refused(self):
        for url in ("http://example.org/a.pdf", "https://localhost/a.pdf", "ftp://example.org/a.pdf",
                    "https://127.0.0.1/a.pdf", "https://10.0.0.5/a.pdf", "https://169.254.169.254/latest",
                    "https://192.168.1.1/x", "https://[::1]/x", ""):
            self.assertFalse(afl.is_safe_url(url), url)

    def test_http_get_refuses_unsafe_url_without_any_request(self):
        with mock.patch("urllib.request.OpenerDirector.open") as opener:
            with self.assertRaises(afl.FetchError):
                afl.http_get("https://127.0.0.1/secret")
        opener.assert_not_called()

    def test_download_accepts_real_pdf_and_rejects_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(afl, "http_get", return_value=b"%PDF-1.4 data"):
                path = afl.download_pdf(paper(title="My: Paper/Title?"), Path(tmp))
            self.assertTrue(path.exists())
            self.assertTrue(path.name.startswith("crossref_10-1-x_"))
            self.assertTrue(path.name.endswith(".pdf"))
            with mock.patch.object(afl, "http_get", return_value=b"<html>paywall</html>"):
                with self.assertRaises(afl.FetchError):
                    afl.download_pdf(paper(), Path(tmp))

    def test_manifest_round_trip_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "manifest.json"
            self.assertEqual(afl.load_manifest(path), {"papers": []})
            manifest = afl.load_manifest(path)
            afl.record_in_manifest(manifest, paper(title="Kept", doi="10.1/k"), 12, path)
            keys = afl.manifest_keys(afl.load_manifest(path))
            self.assertIn("doi:10.1/k", keys)
            self.assertIn("title:kept", keys)
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(afl.load_manifest(path), {"papers": []})



class TaggingTests(unittest.TestCase):
    """The core guardrail: auto-sourced chunks are always identifiable."""

    TAGS = {"source_api": "pubmed", "paper_title": "Nitrification in RAS", "year": 2021}

    def make(self, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return make_pdf(Path(tmp.name) / "p.pdf", [sentences(8)])

    def test_every_chunk_carries_the_strict_tags(self):
        chunks = dp.process_document(self.make(), "auto_sourced_literature", extra_metadata=self.TAGS)
        self.assertGreaterEqual(len(chunks), 1)
        for c in chunks:
            self.assertEqual(c["document_type"], "auto_sourced_literature")
            self.assertEqual(c["source_api"], "pubmed")
            self.assertEqual(c["paper_title"], "Nitrification in RAS")
            self.assertEqual(c["year"], 2021)

    def test_tagging_metadata_from_paper_includes_required_fields(self):
        tags = afl.tagging_metadata(paper(source_api="crossref", title="T", year=2020))
        for key in ("source_api", "paper_title", "year"):
            self.assertIn(key, tags)
        chunks = dp.process_document(self.make(), "auto_sourced_literature", extra_metadata=tags)
        self.assertEqual(chunks[0]["source_api"], "crossref")

    def test_missing_or_invalid_tags_are_refused(self):
        pdf = self.make()
        bad = [
            {},
            {**self.TAGS, "source_api": "google"},
            {**self.TAGS, "paper_title": "  "},
            {k: v for k, v in self.TAGS.items() if k != "year"},
            {**self.TAGS, "year": "2021"},
            {**self.TAGS, "year": True},
            {**self.TAGS, "year": 1500},
            {**self.TAGS, "year": 2999},
        ]
        for tags in bad:
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                dp.process_document(pdf, "auto_sourced_literature", extra_metadata=tags)

    def test_metadata_cannot_override_core_fields_or_use_non_scalars(self):
        pdf = self.make()
        for tags in ({**self.TAGS, "document_type": "trial_data"}, {**self.TAGS, "text": "x"},
                     {**self.TAGS, "chunk_id": "x"}, {**self.TAGS, "authors": ["a"]}):
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                dp.process_document(pdf, "auto_sourced_literature", extra_metadata=tags)

    def test_manual_cli_types_do_not_include_the_auto_type(self):
        self.assertNotIn("auto_sourced_literature", dp.DOCUMENT_TYPES)
        self.assertIn("auto_sourced_literature", dp.ALL_DOCUMENT_TYPES)
        self.assertEqual(retrieval.PUBLIC_LITERATURE_TYPE, dp.AUTO_SOURCED_TYPE)

    def test_internal_types_still_work_without_extra_metadata(self):
        chunks = dp.process_document(self.make(), "trial_data")
        self.assertNotIn("source_api", chunks[0])
        self.assertEqual(chunks[0]["document_type"], "trial_data")


class RetrievalLabelTests(unittest.TestCase):
    def test_public_literature_is_labelled_distinctly_from_internal_data(self):
        matches = [
            {"id": "1", "score": 0.9, "metadata": {
                "text": "Public finding.", "source_file": "x.pdf", "page_number": 3,
                "document_type": "auto_sourced_literature", "paper_title": "Nitrification in RAS", "year": 2021}},
            {"id": "2", "score": 0.8, "metadata": {
                "text": "Internal finding.", "source_file": "NUS.pdf", "page_number": 1,
                "document_type": "trial_data"}},
        ]
        result = retrieval._format_matches(matches)
        self.assertIn("[1] PUBLIC LITERATURE, not Century Aqua data | Nitrification in RAS (2021)", result.context)
        self.assertIn("[2] NUS.pdf | type: trial_data", result.context)
        note = retrieval.format_sources_note(result.sources)
        self.assertIn("- Nitrification in RAS (2021) (public literature, p. 3)", note)
        self.assertIn("- NUS.pdf (trial_data, p. 1)", note)

    def test_cao_prompt_tells_the_model_public_literature_is_not_company_data(self):
        from app.agents import cao_agent

        self.assertIn("PUBLIC LITERATURE", cao_agent.RAG_INSTRUCTIONS)
        self.assertIn("NOT Century Aqua data", cao_agent.RAG_INSTRUCTIONS)
        self.assertIn("never use them as evidence about the SND Probiotic", cao_agent.RAG_INSTRUCTIONS)



FAKE_ENV = {"OPENAI_API_KEY": "sk-test", "PINECONE_API_KEY": "pcsk_t", "PINECONE_INDEX": "idx"}


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    """download -> ingest (real chunking, fake OpenAI/Pinecone) -> manifest."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, FAKE_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def fake_download(self, p):
        return make_pdf(self.dir / f"{p.paper_id.replace('/', '_')}.pdf", [sentences(10)])

    async def test_chunks_reach_pinecone_with_strict_tags_and_pdf_is_deleted(self):
        from app.rag import ingestion

        stored: list[dict] = []

        async def fake_embed(texts):
            return [[0.1, 0.2] for _ in texts]

        async def fake_upsert(chunks):
            stored.extend(chunks)
            return len(chunks)

        manifest = {"papers": []}
        p = paper(title="Open paper", year=2021, doi="10.1/open")
        with mock.patch.object(ingestion, "init_pinecone"), \
             mock.patch.object(ingestion, "generate_embeddings_batch", fake_embed), \
             mock.patch.object(ingestion, "upsert_chunks", fake_upsert), \
             mock.patch.object(afl, "record_in_manifest", lambda m, pp, n: m["papers"].append(pp.title)):
            total, failures = await afl.ingest_papers(
                [p], manifest, lambda m: None, download=self.fake_download
            )
        self.assertEqual(failures, [])
        self.assertGreater(total, 0)
        self.assertEqual(len(stored), total)
        for c in stored:
            self.assertEqual(c["document_type"], "auto_sourced_literature")
            self.assertEqual(
                (c["source_api"], c["paper_title"], c["year"]), ("crossref", "Open paper", 2021)
            )
            self.assertEqual(c["embedding"], [0.1, 0.2])
        self.assertEqual(list(self.dir.glob("*.pdf")), [])  # PDF removed after ingest
        self.assertEqual(manifest["papers"], ["Open paper"])

    async def test_one_failure_does_not_stop_the_others_and_pdf_still_removed(self):
        results: list[str] = []

        def download(p):
            if p.title == "Bad":
                raise afl.FetchError("not a PDF")
            return self.fake_download(p)

        async def ingest(path, doc_type, log, extra_metadata):
            results.append(extra_metadata["paper_title"])
            return SimpleNamespace(chunks=5)

        manifest = {"papers": []}
        with mock.patch.object(afl, "record_in_manifest", lambda m, p, n: m["papers"].append(p.title)):
            total, failures = await afl.ingest_papers(
                [paper(title="Bad", doi="10.1/bad"), paper(title="Good", doi="10.1/good")],
                manifest, lambda m: None, download=download, ingest=ingest,
            )
        self.assertEqual(results, ["Good"])
        self.assertEqual(total, 5)
        self.assertEqual(len(failures), 1)
        self.assertIn("Bad", failures[0])
        self.assertEqual(manifest["papers"], ["Good"])  # failed paper is NOT remembered as done
        self.assertEqual(list(self.dir.glob("*.pdf")), [])



class CliTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, FAKE_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mpath = Path(self.tmp.name) / "manifest.json"
        self.ingested: list[list[afl.Paper]] = []
        self.selected_args: list = []

    def select(self, papers):
        def fake(queries, limit, sources, known, log):
            self.selected_args.append((queries, limit, sources))
            return papers[:limit]

        return fake

    def ingest(self, papers, manifest, log):
        self.ingested.append(papers)
        return asyncio.sleep(0, result=(7 * len(papers), []))

    def run_cli(self, argv, answer="y", papers=None):
        if papers is None:
            papers = [paper(title=f"P{i}", doi=f"10.1/{i}") for i in range(5)]
        prompts: list[str] = []

        def input_fn(prompt):
            prompts.append(prompt)
            return answer

        code = afl.main(argv, input_fn=input_fn, select=self.select(papers),
                        ingest_fn=self.ingest, manifest_path=self.mpath)
        return code, prompts

    def test_default_limit_is_three_and_prompt_shows_the_count(self):
        code, prompts = self.run_cli([])
        self.assertEqual(code, 0)
        self.assertEqual(self.selected_args[0][1], 3)
        self.assertEqual(len(self.ingested[0]), 3)
        self.assertIn("Found 3 open-access papers. Proceed? (y/n)", prompts[0])

    def test_declining_downloads_and_embeds_nothing(self):
        for answer in ("n", "", "no", "maybe"):
            self.ingested.clear()
            code, _ = self.run_cli([], answer=answer)
            self.assertEqual(code, 0)
            self.assertEqual(self.ingested, [], answer)

    def test_yes_flag_skips_the_question(self):
        code, prompts = self.run_cli(["--yes", "--limit", "2"])
        self.assertEqual((code, prompts, len(self.ingested[0])), (0, [], 2))

    def test_dry_run_never_asks_or_ingests_and_needs_no_keys(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "", "PINECONE_API_KEY": ""}):
            code, prompts = self.run_cli(["--dry-run"])
        self.assertEqual((code, prompts, self.ingested), (0, [], []))

    def test_missing_keys_stop_a_real_run_before_any_search(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            code, _ = self.run_cli([])
        self.assertEqual(code, 1)
        self.assertEqual(self.selected_args, [])

    def test_limit_is_bounded(self):
        for bad in ("0", "-1", str(afl.MAX_LIMIT + 1)):
            code, _ = self.run_cli(["--limit", bad])
            self.assertEqual(code, 2, bad)
        self.assertEqual(self.run_cli(["--limit", str(afl.MAX_LIMIT), "--dry-run"])[0], 0)

    def test_nothing_found_exits_cleanly(self):
        code, prompts = self.run_cli([], papers=[])
        self.assertEqual((code, prompts, self.ingested), (0, [], []))

    def test_source_and_custom_queries_are_passed_through(self):
        self.run_cli(["--source", "pubmed", "--query", "tilapia vaccine", "--query", "RAS", "--dry-run"])
        queries, _, sources = self.selected_args[0]
        self.assertEqual((queries, sources), (("tilapia vaccine", "RAS"), ("pubmed",)))
        self.run_cli(["--dry-run"])
        self.assertEqual(self.selected_args[1][0], afl.SEARCH_QUERIES)
        self.assertEqual(self.selected_args[1][2], ("crossref", "pubmed"))

    def test_failed_paper_gives_exit_code_1(self):
        def failing(papers, manifest, log):
            return asyncio.sleep(0, result=(0, ["P0: FetchError: HTTP 404"]))

        code = afl.main([], input_fn=lambda p: "y", select=self.select([paper()]),
                        ingest_fn=failing, manifest_path=self.mpath)
        self.assertEqual(code, 1)

    def test_default_queries_match_the_brief(self):
        for q in ("biofloc tilapia water quality", "simultaneous nitrification denitrification aquaculture",
                  "recirculating aquaculture system ammonia control"):
            self.assertIn(q, afl.SEARCH_QUERIES)


if __name__ == "__main__":
    unittest.main()

