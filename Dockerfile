# ind-trader — one image for CLI + scheduled ETL.
# Usage:
#   docker build -t indtrader .
#   docker run --rm -v %cd%/data:/app/data indtrader backtest RELIANCE --strategy trend_sma200 --costs
#   docker compose up etl-once     (fetch universe once)
#   docker compose up -d etl-loop  (daily 16:05 IST fetch)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# non-root (works on Docker Desktop for Windows too)
RUN useradd -m appuser

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .[nse]

USER appuser
ENV HOME=/home/appuser
WORKDIR /home/appuser

HEALTHCHECK --interval=12h --timeout=5s CMD python -c "import indtrader; print(indtrader.__version__)" || exit 1

ENTRYPOINT []
CMD ["python", "-m", "indtrader.scheduler.run_daily"]
