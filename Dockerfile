FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY data_gen ./data_gen
COPY engine ./engine
COPY feeds ./feeds
COPY highlights ./highlights
COPY vision ./vision
COPY scripts ./scripts
COPY api ./api
# Bake the prototype's three-goal match (the default) and the synthetic one (events + player and ball
# tracking) so the preview starts offline and a cold start does not rebuild them. Nothing is downloaded.
# MATCH_SEED=7 is the published demo.
ENV MATCH_SOURCE=proto
ENV MATCH_SEED=7
RUN python -c "from feeds import ensure_materialized; ensure_materialized('proto'); ensure_materialized('synthetic')"
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
