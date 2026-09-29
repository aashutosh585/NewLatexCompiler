"""
Security validators — blocks path traversal, absolute paths, and shell-escape attempts.
"""

import re

# Patterns that indicate malicious input
_RE_SHELL_ESCAPE = re.compile(
    r"\\(write18|immediate\\write18|directlua|csname\s+@@end)",
    re.IGNORECASE,
)
_RE_ABSOLUTE_PATH = re.compile(
    r"\\(input|include|includegraphics|bibliography|addbibresource|usepackage)"
    r"\s*(\[.*?\])?\s*\{(/|\\\\|[a-zA-Z]:)",
    re.IGNORECASE,
)
_RE_TRAVERSAL = re.compile(
    r"\\(input|include|includegraphics|bibliography|addbibresource)\s*(\[.*?\])?\s*\{[^}]*\.\.",
    re.IGNORECASE,
)

# Special characters that are often unescaped by users
_UNESCAPED_SPECIALS = re.compile(
    r"(?<!\\)([&%$#_])"  # matches & % $ # _ not preceded by backslash
)


def validate_security(latex: str) -> list[str]:
    """
    Validate LaTeX source for security issues.
    Returns a list of rejection reasons (empty = safe).
    """
    issues: list[str] = []

    if _RE_SHELL_ESCAPE.search(latex):
        issues.append("Shell escape commands (\\write18 / \\directlua) are blocked.")

    if _RE_ABSOLUTE_PATH.search(latex):
        issues.append(
            "Absolute paths in \\input/\\include are blocked. "
            "Use relative paths to files provided in the request."
        )

    if _RE_TRAVERSAL.search(latex):
        issues.append(
            "Path traversal (..) in \\input/\\include is blocked. "
            "Only relative paths within the working directory are allowed."
        )

    return issues


def validate_filenames(filenames: list[str]) -> list[str]:
    """
    Validate that provided filenames don't escape the temp dir.
    Returns a list of rejection reasons (empty = safe).
    """
    issues: list[str] = []
    for name in filenames:
        if name.startswith("/") or name.startswith("\\"):
            issues.append(f"Absolute path rejected: {name}")
        if ".." in name:
            issues.append(f"Path traversal rejected: {name}")
        if "\x00" in name:
            issues.append(f"Null byte in filename rejected: {name}")
    return issues


def lint_latex(latex: str) -> list[dict]:
    """
    Fast pre-check for common LaTeX mistakes without compiling.
    Returns a list of lint warnings with line numbers.
    """
    warnings: list[dict] = []
    lines = latex.split("\n")

    brace_depth = 0
    in_verbatim = False

    for line_num, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Track verbatim environments
        if r"\begin{verbatim}" in stripped or r"\begin{lstlisting}" in stripped:
            in_verbatim = True
            continue
        if r"\end{verbatim}" in stripped or r"\end{lstlisting}" in stripped:
            in_verbatim = False
            continue
        if in_verbatim:
            continue

        # Skip comments
        comment_idx = stripped.find("%")
        if comment_idx == 0:
            continue
        # Remove inline comments (not preceded by \)
        active = stripped
        for idx, ch in enumerate(active):
            if ch == "%" and (idx == 0 or active[idx - 1] != "\\"):
                active = active[:idx]
                break

        # Brace counting
        for ch in active:
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth < 0:
                    warnings.append({
                        "line": line_num,
                        "message": "Extra closing brace '}' — no matching opening brace.",
                        "severity": "error",
                    })
                    brace_depth = 0

    if brace_depth > 0:
        warnings.append({
            "line": len(lines),
            "message": f"Unmatched opening brace(s): {brace_depth} unclosed '{{' remain.",
            "severity": "error",
        })

    return warnings
