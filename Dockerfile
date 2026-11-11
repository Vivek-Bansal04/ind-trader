# ind-trader — one image for the WHOLE project: CLI, scheduled ETL, dashboard.
# Everything runs from this image with nothing else installed on the host:
#   docker compose up -d          (etl-loop + dashboard; data persists in ./data)
#   docker compose run --rm cli health
#   docker compose run --rm etl-once
# Plain docker:
#   docker build -t indtrader .
#   docker run --rm -v ./data:/home/appuser/data indtrader python -m indtrader.cli health
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# non-root (works on Docker Desktop for Windows too)
RUN useradd -m appuser

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
# [nse] = the NSE lib, [web] = curl_cffi Chrome transport — without these the
# container would be a datacenter-IP box with a silently-deep-sixed news feed.
RUN pip install .[nse,web]

USER appuser
ENV HOME=/home/appuser
WORKDIR /home/appuser

HEALTHCHECK --interval=12h --timeout=5s CMD python -c "import indtrader; print(indtrader.__version__)" || exit 1

ENTRYPOINT []
CMD ["python", "-m", "indtrader.scheduler.run_daily"]
