# LangGraph Memory Server — serves the Deep Agents memory graph for episodic
# retrieval. `langgraph dev` hosts it; the Trajecta server talks to it via
# langgraph-sdk (`threads.search`).
FROM python:3.12

WORKDIR /app

# Runtime deps for the memory graph + the langgraph dev server.
# (requirements-memory.txt lives beside this Dockerfile in infra-docker/langgraph/.)
COPY infra-docker/langgraph/requirements-memory.txt ./requirements-memory.txt
RUN pip install --no-cache-dir -r requirements-memory.txt "langgraph-cli[inmem]"

# Source tree (not installed as a package — langgraph.json + PYTHONPATH suffice).
COPY server ./server
COPY langgraph.json ./langgraph.json

ENV PYTHONPATH=/app
ENV TRAJECTA_LANGGRAPH_DB=/data/langgraph.db
VOLUME ["/data"]

EXPOSE 2024

CMD ["langgraph", "dev", "--host", "0.0.0.0", "--port", "2024", "--no-browser"]