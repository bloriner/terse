# Terse MCP server — stdlib-only, no build step.
FROM python:3.12-slim
WORKDIR /app
COPY . .
# The server speaks MCP over stdio (NDJSON JSON-RPC); stdlib only.
CMD ["python", "-m", "mcp.server"]
