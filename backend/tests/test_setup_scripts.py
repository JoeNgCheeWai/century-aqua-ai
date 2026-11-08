"""
Mock tests for scripts/setup_pinecone.py and scripts/setup_openai.py.

NO network calls: the Pinecone and OpenAI clients are fakes, and the key prompt
is replaced by a scripted function. The real exception classes from the
installed packages are used, so the error handling is checked against what the
libraries actually raise.

Run from the backend folder:
    venv\\Scripts\\python.exe -m unittest tests.test_setup_scripts -v
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import openai
import pinecone

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = str(BACKEND_DIR / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import setup_openai  # noqa: E402
import setup_pinecone  # noqa: E402

PC_KEY = "pcsk_SECRET1234567890"
OAI_KEY = "sk-proj-SECRET1234567890"


def bare(cls, message="error text", **attrs):
    """Build an exception without its constructor (some need HTTP objects)."""
    exc = cls.__new__(cls)
    Exception.__init__(exc, message)
    for name, value in attrs.items():
        setattr(exc, name, value)
    return exc


def scripted(*answers: str):
    """A getpass replacement that returns the answers in order."""
    remaining = list(answers)
    asked: list[str] = []

    def fake(prompt: str) -> str:
        asked.append(prompt)
        return remaining.pop(0)

    fake.asked = asked  # type: ignore[attr-defined]
    return fake


def run_main(module, getpass_fn, factory=None, saved=None):
    """Run a script's main(). `saved` fakes a key found in .env (default: none),
    so a developer's real backend/.env can never leak into the tests."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = module.main(
            getpass_fn=getpass_fn, client_factory=factory, saved_key_fn=lambda: saved
        )
    return code, out.getvalue()


class FakePinecone:
    def __init__(self, existing=(), dimension=1536, list_error=None, create_error=None):
        self.existing = list(existing)
        self.dimension = dimension
        self.list_error = list_error
        self.create_error = create_error
        self.created: list[dict] = []

    def list_indexes(self):
        if self.list_error:
            raise self.list_error
        return SimpleNamespace(names=lambda: list(self.existing))

    def create_index(self, **kwargs):
        if self.create_error:
            raise self.create_error
        self.created.append(kwargs)

    def describe_index(self, name):
        return SimpleNamespace(dimension=self.dimension)


class SetupPineconeTests(unittest.TestCase):
    def go(self, fake: FakePinecone, *answers: str):
        keys: list[str] = []

        def factory(key):
            keys.append(key)
            return fake

        code, out = run_main(setup_pinecone, scripted(*(answers or (PC_KEY,))), factory)
        self.assertNotIn(PC_KEY, out)  # the key must never be printed
        return code, out, keys

    def test_creates_index_with_exact_specs_and_prints_steps(self):
        fake = FakePinecone()
        code, out, keys = self.go(fake)
        self.assertEqual(code, 0)
        self.assertEqual(keys, [PC_KEY])
        spec = fake.created[0]
        self.assertEqual(spec["name"], "century-aqua-rag")
        self.assertEqual(spec["dimension"], 1536)
        self.assertEqual(spec["metric"], "cosine")
        self.assertEqual(spec["spec"].cloud, "aws")
        self.assertEqual(spec["spec"].region, "us-east-1")
        self.assertIn("Creating index...", out)
        self.assertIn("✅ Index created successfully!", out)
        self.assertIn("PINECONE_INDEX=century-aqua-rag", out)
        self.assertIn("OPENAI_API_KEY", out)
        self.assertIn("did not save your API key", out)

    def test_existing_index_exits_successfully_without_creating(self):
        fake = FakePinecone(existing=["other", "century-aqua-rag"])
        code, out, _ = self.go(fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.created, [])
        self.assertIn("already exists", out)
        self.assertNotIn("Creating index", out)

    def test_existing_index_with_wrong_dimension_fails(self):
        fake = FakePinecone(existing=["century-aqua-rag"], dimension=768)
        code, out, _ = self.go(fake)
        self.assertEqual(code, 1)
        self.assertIn("768", out)

    def test_race_conflict_counts_as_existing(self):
        fake = FakePinecone(create_error=bare(pinecone.ConflictError))
        code, out, _ = self.go(fake)
        self.assertEqual(code, 0)
        self.assertIn("already exists", out)

    def test_bad_prefix_is_rejected_then_accepted(self):
        getpass_fn = scripted("sk-wrong", "", PC_KEY)
        fake = FakePinecone(existing=["century-aqua-rag"])
        code, out = run_main(setup_pinecone, getpass_fn, lambda k: fake)
        self.assertEqual(code, 0)
        self.assertEqual(len(getpass_fn.asked), 3)
        self.assertIn("Enter your Pinecone API key (starts with pcsk_): ", getpass_fn.asked[0])
        self.assertIn("must start with pcsk_", out)

    def test_three_bad_keys_give_up_without_any_client(self):
        def factory(_key):
            raise AssertionError("client must not be created for an invalid key")

        code, out = run_main(setup_pinecone, scripted("a", "b", "pcsk_"), factory)
        self.assertEqual(code, 2)
        self.assertIn("Too many invalid attempts", out)

    def test_key_is_trimmed(self):
        fake = FakePinecone(existing=["century-aqua-rag"])
        _, _, keys = self.go(fake, f"  {PC_KEY}\n")
        self.assertEqual(keys, [PC_KEY])

    def test_invalid_key_error_is_clear(self):
        fake = FakePinecone(list_error=pinecone.UnauthorizedError())
        code, out, _ = self.go(fake)
        self.assertEqual(code, 1)
        self.assertIn("rejected the API key", out)

    def test_network_errors_suggest_checking_connection(self):
        for error in (
            bare(pinecone.PineconeConnectionError),
            bare(pinecone.PineconeTimeoutError),
        ):
            with self.subTest(error=type(error).__name__):
                code, out, _ = self.go(FakePinecone(list_error=error))
                self.assertEqual(code, 1)
                self.assertIn("internet connection", out)

    def test_forbidden_and_payment_errors(self):
        code, out, _ = self.go(FakePinecone(create_error=pinecone.ForbiddenError()))
        self.assertEqual(code, 1)
        self.assertIn("not allowed", out)
        code, out, _ = self.go(FakePinecone(create_error=bare(pinecone.PaymentRequiredError)))
        self.assertEqual(code, 1)
        self.assertIn("billing or plan", out)

    def test_other_pinecone_error_never_leaks_the_key(self):
        error = bare(pinecone.PineconeError, f"boom {PC_KEY} boom")
        code, out, _ = self.go(FakePinecone(list_error=error))
        self.assertEqual(code, 1)
        self.assertIn("boom", out)
        self.assertIn("***", out)

    def test_ctrl_c_at_prompt_and_during_work(self):
        def interrupted(_prompt):
            raise KeyboardInterrupt

        code, _ = run_main(setup_pinecone, interrupted, lambda k: FakePinecone())
        self.assertEqual(code, 130)
        code, _, _ = self.go(FakePinecone(list_error=KeyboardInterrupt()))
        self.assertEqual(code, 130)



class FakeOpenAI:
    def __init__(self, error=None, dimensions=1536):
        self.error = error
        self.dimensions = dimensions
        self.calls: list[dict] = []
        self.embeddings = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * self.dimensions)])


class SetupOpenAITests(unittest.TestCase):
    def go(self, fake: FakeOpenAI, *answers: str):
        keys: list[str] = []

        def factory(key):
            keys.append(key)
            return fake

        code, out = run_main(setup_openai, scripted(*(answers or (OAI_KEY,))), factory)
        self.assertNotIn(OAI_KEY, out)  # the key must never be printed
        return code, out, keys

    def test_success_makes_one_tiny_call_and_does_not_save_the_key(self):
        fake = FakeOpenAI()
        code, out, keys = self.go(fake)
        self.assertEqual(code, 0)
        self.assertEqual(keys, [OAI_KEY])
        self.assertEqual(fake.calls, [{"model": "text-embedding-3-small", "input": "test"}])
        self.assertIn("✅ OpenAI key works!", out)
        self.assertIn("1536-dimension", out)
        self.assertIn("did not save your API key", out)

    def test_nothing_is_written_to_disk(self):
        env_file = BACKEND_DIR / ".env"
        before = env_file.read_bytes() if env_file.exists() else None
        self.go(FakeOpenAI())
        after = env_file.read_bytes() if env_file.exists() else None
        self.assertEqual(before, after)

    def test_prefix_validation_and_retry(self):
        getpass_fn = scripted("pcsk_wrong", OAI_KEY)
        code, out = run_main(setup_openai, getpass_fn, lambda k: FakeOpenAI())
        self.assertEqual(code, 0)
        self.assertIn("Enter your OpenAI API key (starts with sk-): ", getpass_fn.asked[0])
        self.assertIn("must start with sk-", out)

    def test_three_bad_keys_give_up_without_any_client(self):
        def factory(_key):
            raise AssertionError("client must not be created for an invalid key")

        code, out = run_main(setup_openai, scripted("a", "b", "sk-"), factory)
        self.assertEqual(code, 2)
        self.assertIn("Too many invalid attempts", out)

    def test_authentication_error_is_clear(self):
        err = bare(openai.AuthenticationError, f"bad key {OAI_KEY}")
        code, out, _ = self.go(FakeOpenAI(error=err))
        self.assertEqual(code, 1)
        self.assertIn("rejected the API key", out)

    def test_network_and_timeout_errors_suggest_checking_connection(self):
        for cls in (openai.APIConnectionError, openai.APITimeoutError):
            with self.subTest(error=cls.__name__):
                code, out, _ = self.go(FakeOpenAI(error=bare(cls)))
                self.assertEqual(code, 1)
                self.assertIn("internet connection", out)

    def test_quota_vs_rate_limit(self):
        quota = bare(openai.RateLimitError, code="insufficient_quota")
        code, out, _ = self.go(FakeOpenAI(error=quota))
        self.assertEqual(code, 1)
        self.assertIn("no credit", out)
        limited = bare(openai.RateLimitError, code="rate_limit")
        code, out, _ = self.go(FakeOpenAI(error=limited))
        self.assertIn("rate-limiting", out)

    def test_permission_denied(self):
        code, out, _ = self.go(FakeOpenAI(error=bare(openai.PermissionDeniedError)))
        self.assertEqual(code, 1)
        self.assertIn("not allowed", out)

    def test_other_api_error_never_leaks_the_key(self):
        err = bare(openai.APIError, f"weird {OAI_KEY} weird")
        code, out, _ = self.go(FakeOpenAI(error=err))
        self.assertEqual(code, 1)
        self.assertIn("weird", out)
        self.assertIn("***", out)

    def test_unexpected_dimension_fails(self):
        code, out, _ = self.go(FakeOpenAI(dimensions=768))
        self.assertEqual(code, 1)
        self.assertIn("Expected 1536", out)

    def test_ctrl_c(self):
        def interrupted(_prompt):
            raise KeyboardInterrupt

        code, _ = run_main(setup_openai, interrupted, lambda k: FakeOpenAI())
        self.assertEqual(code, 130)


class SavedKeyTests(unittest.TestCase):
    def test_saved_key_skips_the_prompt_and_is_never_printed(self):
        def no_prompt(_p):
            raise AssertionError("must not prompt when a key is saved")

        fake = FakePinecone(existing=["century-aqua-rag"])
        code, out = run_main(setup_pinecone, no_prompt, lambda k: fake, saved=PC_KEY)
        self.assertEqual(code, 0)
        self.assertIn("from backend\\.env", out)
        self.assertNotIn(PC_KEY, out)

        code, out = run_main(setup_openai, no_prompt, lambda k: FakeOpenAI(), saved=OAI_KEY)
        self.assertEqual(code, 0)
        self.assertIn("from backend\\.env", out)
        self.assertNotIn(OAI_KEY, out)

    def test_saved_key_is_what_reaches_the_client(self):
        keys: list[str] = []

        def factory(k):
            keys.append(k)
            return FakePinecone(existing=["century-aqua-rag"])

        run_main(setup_pinecone, scripted(), factory, saved=PC_KEY)
        self.assertEqual(keys, [PC_KEY])

    def test_without_saved_key_the_prompt_and_paste_tip_appear(self):
        fake = FakePinecone(existing=["century-aqua-rag"])
        code, out = run_main(setup_pinecone, scripted(PC_KEY), lambda k: fake)
        self.assertEqual(code, 0)
        self.assertIn("cannot paste", out)
        self.assertIn("PINECONE_API_KEY=", out)

    def test_find_saved_key_reads_env_file_and_ignores_placeholders(self):
        import os
        import tempfile
        from unittest import mock

        from _keys import find_saved_key

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ):
            os.environ.pop("PINECONE_API_KEY", None)
            env = Path(tmp) / ".env"
            env.write_text(f'PINECONE_API_KEY="  {PC_KEY} "\n', encoding="utf-8")
            self.assertEqual(find_saved_key("PINECONE_API_KEY", "pcsk_", env), PC_KEY)
            env.write_text("PINECONE_API_KEY=your-key-here\n", encoding="utf-8")
            self.assertIsNone(find_saved_key("PINECONE_API_KEY", "pcsk_", env))
            env.write_text("PINECONE_API_KEY=\n", encoding="utf-8")
            self.assertIsNone(find_saved_key("PINECONE_API_KEY", "pcsk_", env))
            self.assertIsNone(find_saved_key("PINECONE_API_KEY", "pcsk_", Path(tmp) / "none"))
            os.environ["PINECONE_API_KEY"] = PC_KEY  # the environment wins
            self.assertEqual(find_saved_key("PINECONE_API_KEY", "pcsk_", env), PC_KEY)


class ConstantsMatchTheAppTests(unittest.TestCase):
    def test_dimension_and_model_match_the_rag_code(self):
        from app.rag import embeddings

        self.assertEqual(setup_pinecone.DIMENSION, embeddings.EMBEDDING_DIMENSIONS)
        self.assertEqual(setup_openai.EMBEDDING_MODEL, embeddings.EMBEDDING_MODEL)
        self.assertEqual(setup_openai.EXPECTED_DIMENSIONS, embeddings.EMBEDDING_DIMENSIONS)


if __name__ == "__main__":
    unittest.main()

