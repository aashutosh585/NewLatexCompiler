# NewLatexCompiler

Standalone LaTeX → PDF compiler microservice. FastAPI + TeX Live slim, Dockerized, sync-only.

## Quick Start

### With Docker (recommended)

```bash
# Build and run
docker compose up --build

# Service is now at http://localhost:3002
# API docs at http://localhost:3002/docs
```

### Without Docker (local dev, requires TeX Live installed)

```bash
pip install -r requirements.txt
python main.py
```

---

## API Reference

### `GET /health`

Returns engine versions and service status.

```bash
curl http://localhost:3002/health
```

```json
{
  "status": "healthy",
  "engines": {
    "pdflatex": "pdfTeX 3.14159265-2.6-1.40.25 (TeX Live 2024)",
    "xelatex": "XeTeX 3.141592653-2.6-0.999996 (TeX Live 2024)",
    "bibtex": "BibTeX 0.99d (TeX Live 2024)"
  }
}
```

### `POST /compile`

Compile LaTeX source to PDF.

```bash
# Simple compile
curl -X POST http://localhost:3002/compile \
  -H "Content-Type: application/json" \
  -d '{"latex": "\\documentclass{article}\\begin{document}Hello\\end{document}"}' \
  -o output.pdf

# With xelatex engine
curl -X POST http://localhost:3002/compile \
  -H "Content-Type: application/json" \
  -d '{"latex": "...", "engine": "xelatex"}' \
  -o output.pdf

# Multi-file project
curl -X POST http://localhost:3002/compile \
  -H "Content-Type: application/json" \
  -d '{
    "latex": "",
    "main": "resume.tex",
    "files": {
      "resume.tex": "\\documentclass{article}\\input{header}\\begin{document}...\\end{document}",
      "header.tex": "\\usepackage{geometry}"
    }
  }' \
  -o output.pdf
```

**Success (200):** Returns raw PDF bytes with `Content-Type: application/pdf`.

**Error (422):**
```json
{
  "error": "Unknown command — check spelling or missing \\usepackage.",
  "line": 15,
  "raw": "Undefined control sequence",
  "log_excerpt": "..."
}
```

### `POST /validate`

Fast lint check without compiling.

```bash
curl -X POST http://localhost:3002/validate \
  -H "Content-Type: application/json" \
  -d '{"latex": "\\documentclass{article}\\begin{document}\\textbf{oops\\end{document}"}'
```

```json
{
  "valid": false,
  "security_issues": [],
  "lint": [
    {"line": 1, "message": "Unmatched opening brace(s): 1 unclosed '{' remain.", "severity": "error"}
  ]
}
```

---

## Authentication

Set `COMPILER_SECRET` in `.env`. Pass the secret via header:

```bash
curl -X POST http://localhost:3002/compile \
  -H "X-Compiler-Secret: your-secret-here" \
  -H "Content-Type: application/json" \
  -d '{"latex": "..."}' \
  -o output.pdf
```

Leave `COMPILER_SECRET` empty to disable auth (local dev only).

---

## Security Guarantees

| Threat | Mitigation |
|--------|-----------|
| Shell escape (`\write18`) | `-no-shell-escape` flag + pre-compilation regex check |
| File read (`\input{/etc/passwd}`) | Absolute path regex block + temp dir isolation |
| Path traversal (`\input{../../..}`) | `..` regex block in all `\input`/`\include` |
| DoS (infinite loop) | 20s hard timeout per pass, process killed |
| DoS (resource exhaustion) | Docker CPU (2 cores) + memory (1GB) limits |
| Container escape | Non-root user, optional `network_mode: none` |
| Large payload | 200KB input size limit |

---

## Configuration

All knobs via environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `3002` | Server port |
| `COMPILER_SECRET` | _(empty)_ | Auth token (empty = disabled) |
| `COMPILE_TIMEOUT_SECS` | `20` | Timeout per compilation pass |
| `DEFAULT_ENGINE` | `pdflatex` | Default engine if not specified |
| `MAX_INPUT_BYTES` | `204800` | Max input size (200KB) |
| `CACHE_DIR` | `/tmp/latex-cache` | Disk cache directory |

---

## Extending to LuaLaTeX

Add `texlive-luatex` to the Dockerfile, then add `"lualatex"` to the engine pattern in `main.py`:

```python
engine: str = Field(default="pdflatex", pattern=r"^(pdflatex|xelatex|lualatex)$")
```

No other code changes needed — the compiler module passes the engine name directly to subprocess.

---

## Running Tests

```bash
pip install pytest httpx
pytest test_compiler.py -v
```

> **Note:** Tests that compile LaTeX require TeX Live installed locally. Security and validation tests run without it.

---

## Architecture

```
POST /compile
    │
    ├─ Size check (413 if > 200KB)
    ├─ Security validation (422 if shell-escape / traversal)
    ├─ Cache lookup (SHA-256 of source + engine)
    │   └─ HIT → return cached PDF
    │
    ├─ Create throwaway temp dir
    ├─ Write all files
    ├─ Pass 1: pdflatex/xelatex -interaction=nonstopmode -halt-on-error -no-shell-escape
    ├─ BibTeX pass (if .bib files present)
    ├─ Pass 2: if log says "Rerun to get cross-references"
    ├─ Read output PDF
    │   ├─ SUCCESS → cache + return PDF bytes (200)
    │   └─ FAIL → parse log → structured error (422)
    │
    └─ Delete temp dir (always, finally block)
```
