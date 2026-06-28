FROM python:3.12-slim
# tini as PID 1 reaps the zombies bwrap's in-namespace helper leaves (else they
# pile up to the pids cap and judging fails).
RUN apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap g++ tini \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# ships vendor/testlib.h, which custom checkers compile against at runtime.
COPY . .
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "worker.py"]
