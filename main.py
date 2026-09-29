"""
NewLatexCompiler — FastAPI microservice for LaTeX-to-PDF compilation.
Port 3002 | TeX Live slim | Sync-only
"""

import os
import subprocess

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from compiler import compile_latex
from log_parser import parse_latex_log
from security import lint_latex, validate_filenames, validate_security

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PORT = int(os.getenv("PORT", "3002"))
HOST = os.getenv("HOST", "0.0.0.0")
COMPILER_SECRET = os.getenv("COMPILER_SECRET", "")
MAX_INPUT_BYTES = int(os.getenv("MAX_INPUT_BYTES", "204800"))  # 200KB
COMPILE_TIMEOUT = int(os.getenv("COMPILE_TIMEOUT_SECS", "20"))
DEFAULT_ENGINE = os.getenv("DEFAULT_ENGINE", "pdflatex")
CACHE_DIR = os.getenv("CACHE_DIR", "")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="NewLatexCompiler",
    description="Standalone LaTeX → PDF compiler microservice",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in ("/health", "/docs", "/openapi.json"):
        return await call_next(request)

    if COMPILER_SECRET:
        token = request.headers.get("X-Compiler-Secret", "")
        if token != COMPILER_SECRET:
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    return await call_next(request)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class CompileRequest(BaseModel):
    latex: str = Field(..., description="Full LaTeX source code")
    engine: str = Field(default="pdflatex", pattern=r"^(pdflatex|xelatex)$")
    # Multi-file support
    main: str | None = Field(default=None, description="Main .tex filename if using files dict")
    files: dict[str, str] | None = Field(
        default=None,
        description="Dict of {filename: content} for multi-file projects",
    )


class ValidateRequest(BaseModel):
    latex: str = Field(..., description="LaTeX source to lint")


class CompileErrorDetail(BaseModel):
    error: str
    message: str | None = None
    package: str | None = None
    line: int | None = None
    raw: str | None = None
    file_hint: str | None = None
    log_excerpt: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check — returns engine versions and service status."""
    engines: dict[str, str] = {}
    for eng in ("pdflatex", "xelatex", "bibtex"):
        try:
            result = subprocess.run(
                [eng, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            first_line = result.stdout.split("\n")[0] if result.stdout else "not found"
            engines[eng] = first_line
        except (FileNotFoundError, subprocess.TimeoutExpired):
            engines[eng] = "not installed"

    return {
        "status": "healthy",
        "service": "NewLatexCompiler",
        "port": PORT,
        "engines": engines,
        "default_engine": DEFAULT_ENGINE,
        "max_input_bytes": MAX_INPUT_BYTES,
        "timeout_secs": COMPILE_TIMEOUT,
        "cache_enabled": bool(CACHE_DIR),
    }


@app.post("/compile")
async def compile_endpoint(req: CompileRequest):
    """
    Compile LaTeX source to PDF.

    Returns PDF bytes (200) or structured error (422).
    """
    # --- Input size check ---
    source = req.latex
    total_size = len(source.encode("utf-8"))
    if req.files:
        for content in req.files.values():
            total_size += len(content.encode("utf-8"))
    if total_size > MAX_INPUT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Input exceeds {MAX_INPUT_BYTES} byte limit ({total_size} bytes).",
        )

    # --- Security validation ---
    issues = validate_security(source)
    if req.files:
        for content in req.files.values():
            issues.extend(validate_security(content))
        issues.extend(validate_filenames(list(req.files.keys())))

    if issues:
        raise HTTPException(status_code=422, detail={
            "error": "Security validation failed",
            "issues": issues,
        })

    # --- Determine engine ---
    engine = req.engine or DEFAULT_ENGINE
    if engine not in ("pdflatex", "xelatex"):
        engine = DEFAULT_ENGINE

    # --- Determine main filename ---
    main_filename = req.main or "main.tex"

    # --- Compile ---
    result = await compile_latex(
        source=source,
        engine=engine,
        files=req.files,
        main_filename=main_filename,
        timeout=COMPILE_TIMEOUT,
        cache_dir=CACHE_DIR or None,
    )

    if result.success and result.pdf:
        headers = {
            "X-Cache-Hit": "true" if result.cached else "false",
            "X-Cache-Key": result.cache_key[:16],
            "X-Compile-Engine": engine,
            "Content-Disposition": "inline; filename=output.pdf",
        }
        return Response(
            content=result.pdf,
            media_type="application/pdf",
            headers=headers,
        )

    # --- Error response ---
    first_error = result.errors[0] if result.errors else None
    log_lines = result.log.split("\n")
    # Take ~30 lines around the first error for context
    log_excerpt = "\n".join(log_lines[:50]) if len(log_lines) > 50 else result.log

    if first_error and first_error.file_hint:
        error_detail = CompileErrorDetail(
            error="missing_package",
            package=first_error.file_hint,
            message=f"Package '{first_error.file_hint}' is not installed on this compiler" if first_error.file_hint.endswith(('.sty', '.cls')) else f"File '{first_error.file_hint}' not found",
            line=first_error.line,
            raw=first_error.raw,
            file_hint=first_error.file_hint,
            log_excerpt=log_excerpt[:3000],
        )
    else:
        error_detail = CompileErrorDetail(
            error=first_error.message if first_error else "Compilation failed",
            line=first_error.line if first_error else None,
            raw=first_error.raw if first_error else None,
            file_hint=first_error.file_hint if first_error else None,
            log_excerpt=log_excerpt[:3000],
        )

    return JSONResponse(
        status_code=422,
        content=error_detail.model_dump(exclude_none=True),
    )


@app.post("/validate")
async def validate_endpoint(req: ValidateRequest):
    """
    Fast lint check — catches unbalanced braces, unescaped specials,
    and security issues WITHOUT compiling.
    """
    if len(req.latex.encode("utf-8")) > MAX_INPUT_BYTES:
        raise HTTPException(status_code=413, detail="Input too large")

    security_issues = validate_security(req.latex)
    lint_warnings = lint_latex(req.latex)

    return {
        "valid": len(security_issues) == 0 and all(
            w["severity"] != "error" for w in lint_warnings
        ),
        "security_issues": security_issues,
        "lint": lint_warnings,
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, reload=False, log_level="info")
