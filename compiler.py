"""
Core compiler — runs pdflatex/xelatex in a temp directory with timeout, rerun logic,
and optional bibtex/biber support.
"""

import asyncio
import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from log_parser import LatexError, needs_rerun, parse_latex_log


@dataclass(frozen=True, slots=True)
class CompileResult:
    """Outcome of a LaTeX compilation."""
    success: bool
    pdf: bytes | None = None
    errors: list[LatexError] = field(default_factory=list)
    log: str = ""
    cached: bool = False
    cache_key: str = ""


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(source: str, engine: str, extra_files: dict[str, str] | None) -> str:
    """SHA-256 hash of the full compilation input."""
    h = hashlib.sha256()
    h.update(engine.encode())
    h.update(source.encode())
    if extra_files:
        for name in sorted(extra_files):
            h.update(name.encode())
            h.update(extra_files[name].encode())
    return h.hexdigest()


def _cache_get(cache_dir: str, key: str) -> bytes | None:
    """Return cached PDF bytes or None."""
    path = Path(cache_dir) / f"{key}.pdf"
    if path.is_file():
        return path.read_bytes()
    return None


def _cache_put(cache_dir: str, key: str, pdf: bytes) -> None:
    """Store PDF in the disk cache."""
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    (cache_path / f"{key}.pdf").write_bytes(pdf)


# ---------------------------------------------------------------------------
# Subprocess runner
# ---------------------------------------------------------------------------

async def _run_engine(
    engine: str,
    workdir: str,
    main_file: str,
    timeout: int,
) -> tuple[int, str]:
    """
    Run a LaTeX engine as an async subprocess.
    Returns (exit_code, combined_output).
    """
    cmd = [
        engine,
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-no-shell-escape",
        f"-output-directory={workdir}",
        main_file,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        output = (stdout or b"").decode("utf-8", errors="replace")
        output += (stderr or b"").decode("utf-8", errors="replace")
        return proc.returncode or 1, output

    except asyncio.TimeoutError:
        proc.kill()  # type: ignore[union-attr]
        await proc.wait()  # type: ignore[union-attr]
        return -1, f"Compilation timed out after {timeout}s."

    except FileNotFoundError:
        return -1, f"Engine '{engine}' not found. Is TeX Live installed?"


async def _run_bibtex(workdir: str, main_stem: str, timeout: int) -> str:
    """Run bibtex on the main file stem."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "bibtex", main_stem,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return (stdout or b"").decode("utf-8", errors="replace")
    except (asyncio.TimeoutError, FileNotFoundError):
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def compile_latex(
    source: str,
    engine: str = "pdflatex",
    files: dict[str, str] | None = None,
    main_filename: str = "main.tex",
    timeout: int = 20,
    cache_dir: str | None = None,
) -> CompileResult:
    """
    Compile LaTeX source to PDF.

    Args:
        source: LaTeX source (used as main file if `files` is None).
        engine: 'pdflatex' or 'xelatex'.
        files: Optional dict of {filename: content} for multi-file support.
        main_filename: Which file in `files` to compile (default 'main.tex').
        timeout: Seconds per engine pass before hard-kill.
        cache_dir: Directory for disk-based PDF cache (None = no caching).

    Returns:
        CompileResult with PDF bytes or structured errors.
    """
    # Determine effective file map
    if files:
        all_files = dict(files)
        if main_filename not in all_files:
            all_files[main_filename] = source
    else:
        all_files = {main_filename: source}

    # Cache check
    key = _cache_key(source, engine, files)
    if cache_dir:
        cached_pdf = _cache_get(cache_dir, key)
        if cached_pdf is not None:
            return CompileResult(
                success=True, pdf=cached_pdf, cached=True, cache_key=key
            )

    # Create throwaway temp directory
    workdir = tempfile.mkdtemp(prefix="latexcomp-")
    main_path = os.path.join(workdir, main_filename)
    main_stem = os.path.splitext(main_filename)[0]

    try:
        # Write all files to the temp directory
        for fname, content in all_files.items():
            fpath = os.path.join(workdir, fname)
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)

        # Detect if bibtex is needed
        has_bib = any(fname.endswith(".bib") for fname in all_files)

        # --- Pass 1 ---
        code, log = await _run_engine(engine, workdir, main_path, timeout)

        if code == -1:
            return CompileResult(
                success=False,
                errors=[LatexError(raw="Timeout", message=log, line=None, file_hint=None)],
                log=log,
                cache_key=key,
            )

        # --- BibTeX pass (if .bib files present) ---
        if has_bib and code == 0:
            bib_log = await _run_bibtex(workdir, main_stem, timeout)
            log += "\n--- bibtex ---\n" + bib_log

        # --- Pass 2 (if cross-refs need resolving or bibtex was run) ---
        if (code == 0 and needs_rerun(log)) or has_bib:
            code2, log2 = await _run_engine(engine, workdir, main_path, timeout)
            log += "\n--- pass 2 ---\n" + log2
            if code2 == -1:
                return CompileResult(
                    success=False,
                    errors=[LatexError(
                        raw="Timeout",
                        message=f"Second pass timed out after {timeout}s.",
                        line=None, file_hint=None,
                    )],
                    log=log,
                    cache_key=key,
                )

        # Read output PDF
        pdf_path = os.path.join(workdir, f"{main_stem}.pdf")
        if os.path.isfile(pdf_path):
            pdf_bytes = Path(pdf_path).read_bytes()

            # Read .log file for better error extraction
            log_path = os.path.join(workdir, f"{main_stem}.log")
            if os.path.isfile(log_path):
                log = Path(log_path).read_text("utf-8", errors="replace")

            errors = parse_latex_log(log)

            if pdf_bytes and len(pdf_bytes) > 0:
                # Cache on success
                if cache_dir:
                    _cache_put(cache_dir, key, pdf_bytes)

                return CompileResult(
                    success=True,
                    pdf=pdf_bytes,
                    errors=errors,
                    log=log,
                    cache_key=key,
                )

        # Compilation failed — parse errors from log
        log_path = os.path.join(workdir, f"{main_stem}.log")
        if os.path.isfile(log_path):
            log = Path(log_path).read_text("utf-8", errors="replace")

        errors = parse_latex_log(log)
        if not errors:
            errors = [LatexError(
                raw="Unknown",
                message="Compilation failed but no specific error found in log.",
                line=None,
                file_hint=None,
            )]

        return CompileResult(
            success=False, errors=errors, log=log, cache_key=key
        )

    finally:
        # Always cleanup the temp directory
        shutil.rmtree(workdir, ignore_errors=True)
