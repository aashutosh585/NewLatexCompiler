# ============================================================
# NewLatexCompiler — Dockerfile
# TeX Live slim + Python 3.12 | ~1.5GB image
# ============================================================

FROM python:3.12-slim AS base

# Avoid prompts during package install
ENV DEBIAN_FRONTEND=noninteractive

# Install TeX Live (slim selection) + cleanup in one layer
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        texlive-latex-base \
        texlive-latex-extra \
        texlive-latex-recommended \
        texlive-fonts-recommended \
        texlive-fonts-extra \
        texlive-bibtex-extra \
        texlive-xetex \
        texlive-lang-english \
        texlive-pictures \
        texlive-science \
        lmodern \
        latexmk \
        biber \
        && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Create non-root user for security
RUN groupadd -r compiler && \
    useradd -r -g compiler -m -s /bin/bash compiler

# Set up app directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py compiler.py log_parser.py security.py ./

# Create cache and temp directories owned by non-root user
RUN mkdir -p /tmp/latex-cache && \
    chown -R compiler:compiler /app /tmp/latex-cache

# Switch to non-root user
USER compiler

# Expose port
EXPOSE 3002

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:3002/health')" || exit 1

# Run the server
CMD ["python", "main.py"]
