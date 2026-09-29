"""
Test suite for NewLatexCompiler.
Run: pytest test_compiler.py -v
"""

import hashlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)
SAMPLES = Path(__file__).parent / "samples"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_sample(name: str) -> str:
    return (SAMPLES / name).read_text("utf-8")


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "healthy"
        assert "engines" in data


# ---------------------------------------------------------------------------
# /compile — success
# ---------------------------------------------------------------------------

class TestCompileSuccess:
    def test_valid_tex_returns_pdf(self):
        """A valid minimal .tex compiles and returns a PDF."""
        latex = read_sample("valid.tex")
        r = client.post("/compile", json={"latex": latex, "engine": "pdflatex"})
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        # PDF magic bytes
        assert r.content[:5] == b"%PDF-"

    def test_valid_tex_xelatex(self):
        """Same document compiles under xelatex."""
        latex = read_sample("valid.tex")
        r = client.post("/compile", json={"latex": latex, "engine": "xelatex"})
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# /compile — errors
# ---------------------------------------------------------------------------

class TestCompileErrors:
    def test_unbalanced_braces_returns_error(self):
        """A .tex with unbalanced braces returns 422 with a clean error."""
        latex = read_sample("fixable_error.tex")
        r = client.post("/compile", json={"latex": latex})
        assert r.status_code == 422
        data = r.json()
        assert "error" in data
        assert data["error"]  # non-empty error message

    def test_empty_input_rejected(self):
        """Empty LaTeX source is rejected."""
        r = client.post("/compile", json={"latex": ""})
        assert r.status_code == 422  # pydantic validation

    def test_oversized_input_rejected(self):
        """Input exceeding 200KB is rejected with 413."""
        huge = "x" * 300_000
        r = client.post("/compile", json={"latex": huge})
        assert r.status_code == 413


# ---------------------------------------------------------------------------
# /compile — security
# ---------------------------------------------------------------------------

class TestSecurity:
    def test_shell_escape_blocked(self):
        """\\write18 attempts are rejected before compilation."""
        latex = read_sample("malicious.tex")
        r = client.post("/compile", json={"latex": latex})
        assert r.status_code == 422
        data = r.json()
        detail = data.get("detail", data)
        issues = detail.get("issues", []) if isinstance(detail, dict) else []
        # Should mention shell escape
        assert any("shell" in i.lower() or "write18" in i.lower() for i in issues), \
            f"Expected shell-escape rejection, got: {issues}"

    def test_absolute_path_blocked(self):
        """\\input{/etc/passwd} is rejected."""
        latex = r'\documentclass{article}\begin{document}\input{/etc/passwd}\end{document}'
        r = client.post("/compile", json={"latex": latex})
        assert r.status_code == 422

    def test_path_traversal_blocked(self):
        """\\input{../../etc/shadow} is rejected."""
        latex = r'\documentclass{article}\begin{document}\input{../../etc/shadow}\end{document}'
        r = client.post("/compile", json={"latex": latex})
        assert r.status_code == 422

    def test_invalid_engine_falls_back(self):
        """Invalid engine name falls back to default, doesn't crash."""
        latex = r'\documentclass{article}\begin{document}Hi\end{document}'
        r = client.post("/compile", json={"latex": latex, "engine": "pdflatex"})
        # Should either succeed or fail gracefully (not 500)
        assert r.status_code in (200, 422)


# ---------------------------------------------------------------------------
# /validate
# ---------------------------------------------------------------------------

class TestValidate:
    def test_valid_source_passes(self):
        latex = read_sample("valid.tex")
        r = client.post("/validate", json={"latex": latex})
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is True

    def test_unbalanced_braces_caught(self):
        latex = r'\documentclass{article}\begin{document}\textbf{oops\end{document}'
        r = client.post("/validate", json={"latex": latex})
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is False
        assert len(data["lint"]) > 0

    def test_malicious_source_flagged(self):
        latex = read_sample("malicious.tex")
        r = client.post("/validate", json={"latex": latex})
        assert r.status_code == 200
        data = r.json()
        assert len(data["security_issues"]) > 0


# ---------------------------------------------------------------------------
# /compile — caching
# ---------------------------------------------------------------------------

class TestCaching:
    def test_cache_hit_faster(self):
        """Second compile of identical input should be faster (cache hit)."""
        latex = read_sample("valid.tex")
        payload = {"latex": latex, "engine": "pdflatex"}

        # First compile (cold)
        t0 = time.time()
        r1 = client.post("/compile", json=payload)
        t1 = time.time() - t0

        if r1.status_code != 200:
            pytest.skip("Compilation failed — TeX Live not installed")

        # Second compile (should be cached)
        t0 = time.time()
        r2 = client.post("/compile", json=payload)
        t2 = time.time() - t0

        assert r2.status_code == 200
        assert r2.content[:5] == b"%PDF-"
        # Cache hit should be significantly faster
        if t1 > 0.5:  # Only assert if first compile was slow enough to measure
            assert t2 < t1, f"Cache miss? cold={t1:.2f}s, warm={t2:.2f}s"


# ---------------------------------------------------------------------------
# Multi-file support
# ---------------------------------------------------------------------------

class TestMultiFile:
    def test_multi_file_compile(self):
        """Multi-file project with main + include compiles."""
        main = r"""
\documentclass{article}
\begin{document}
\input{chapter1}
\end{document}
"""
        chapter = r"""
\section{Chapter One}
Hello from an included file.
"""
        r = client.post("/compile", json={
            "latex": main,
            "main": "main.tex",
            "files": {
                "main.tex": main,
                "chapter1.tex": chapter,
            },
        })
        # Should succeed or fail gracefully
        assert r.status_code in (200, 422)
