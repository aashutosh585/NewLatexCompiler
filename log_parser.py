"""
LaTeX log parser — extracts structured errors from raw .log output.
"""

import re
from dataclasses import dataclass

# Common LaTeX errors -> human-readable messages
_ERROR_MAP: dict[str, str] = {
    "Undefined control sequence": "Unknown command — check spelling or missing \\usepackage.",
    "Missing $ inserted": "Math-mode issue — you likely have an unescaped $ or _ outside math.",
    "Missing \\begin{document}": "No \\begin{document} found — is the preamble correct?",
    "File `": "A required file or package is missing.",
    "LaTeX Error: File": "A required file or package is missing.",
    "Emergency stop": "Fatal error — LaTeX could not recover. Check for unbalanced braces.",
    "Too many }'s": "Extra closing brace — check your brace matching.",
    "Runaway argument": "Runaway argument — likely a missing closing brace.",
    "Misplaced alignment tab character &": "Unescaped & outside a table — use \\& in text.",
    "Extra alignment tab": "Too many & columns — check your table structure.",
    "Missing number, treated as zero": "Expected a number (e.g. in \\hspace) but got text.",
    "Illegal unit of measure": "Missing unit — use pt, cm, in, etc. after a dimension.",
    "Package inputenc Error": "Character encoding issue — check for non-UTF-8 characters.",
    "Font": "Font issue — a requested font is not installed.",
}

_RE_ERROR_LINE = re.compile(r"^! (.+)", re.MULTILINE)
_RE_LINE_NUMBER = re.compile(r"^l\.(\d+)", re.MULTILINE)
_RE_FILE_NOT_FOUND = re.compile(r"File [`']([^']+)' not found", re.MULTILINE)
_RE_PACKAGE_ERROR = re.compile(r"^! Package (\w+) Error: (.+)", re.MULTILINE)
_RE_RERUN = re.compile(
    r"(Rerun to get cross-references right|Label\(s\) may have changed|"
    r"Rerun LaTeX|Please rerun LaTeX)",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class LatexError:
    """A single parsed LaTeX error."""
    raw: str
    message: str
    line: int | None
    file_hint: str | None


def _humanize(raw_error: str) -> str:
    """Map a raw LaTeX error string to a friendlier message."""
    for pattern, human_msg in _ERROR_MAP.items():
        if pattern.lower() in raw_error.lower():
            return human_msg
    return raw_error


def needs_rerun(log_text: str) -> bool:
    """Return True if the log indicates cross-references need a second pass."""
    return bool(_RE_RERUN.search(log_text))


def parse_latex_log(log_text: str) -> list[LatexError]:
    """
    Parse a LaTeX log and return structured errors.
    Skips warnings — only returns lines starting with '! '.
    """
    errors: list[LatexError] = []
    lines = log_text.split("\n")

    i = 0
    while i < len(lines):
        line = lines[i]

        if not line.startswith("! "):
            i += 1
            continue

        raw_error = line[2:].strip()

        # Check for package errors with more detail
        pkg_match = _RE_PACKAGE_ERROR.match(line)
        if pkg_match:
            raw_error = f"Package {pkg_match.group(1)}: {pkg_match.group(2)}"

        # Look ahead for line number (l.NN)
        error_line: int | None = None
        file_hint: str | None = None
        for j in range(i + 1, min(i + 10, len(lines))):
            ln_match = re.match(r"^l\.(\d+)\s*(.*)", lines[j])
            if ln_match:
                error_line = int(ln_match.group(1))
                break

        # Check for file-not-found hints
        fnf_match = _RE_FILE_NOT_FOUND.search(log_text[log_text.find(line):])
        if fnf_match:
            file_hint = fnf_match.group(1)

        human_msg = _humanize(raw_error)

        errors.append(LatexError(
            raw=raw_error,
            message=human_msg,
            line=error_line,
            file_hint=file_hint,
        ))

        i += 1

    return errors
