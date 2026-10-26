FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

ENV WOOCOMMERCE_HOST=0.0.0.0
ENV WOOCOMMERCE_PORT=8000
EXPOSE 8000

# FastMCP streamable-http binds via mcp settings; use uvicorn entry if needed.
CMD ["python", "server.py"]
