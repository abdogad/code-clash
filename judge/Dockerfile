FROM python:3.12-slim
# Pinned tallyrun release (the sandbox binary). Bump deliberately: a new version
# can shift the constant bwrap-setup instruction offset, which nudges calibrated
# instruction budgets (INSN_PER_MS in judging.py).
ARG TALLYRUN_VERSION=v0.5.0
# bubblewrap: tallyrun shells out to `bwrap` for isolation. g++: compiles C++
# solutions and testlib checkers inside the sandbox (from the ro-bound /usr).
# tini as PID 1 reaps the zombies bwrap's in-namespace helper leaves (else they
# pile up to the pids cap and judging fails). curl+ca-certificates only to fetch
# the binary; purged after to keep the image lean.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bubblewrap g++ tini curl ca-certificates \
    && curl -fL -o /usr/local/bin/tallyrun \
        "https://github.com/abdogad/tallyrun/releases/download/${TALLYRUN_VERSION}/tallyrun-x86_64-unknown-linux-musl" \
    && chmod +x /usr/local/bin/tallyrun \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# ships vendor/testlib.h, which custom checkers compile against at runtime.
COPY . .
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "worker.py"]
