FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
COPY data/ ./data/

ENV SHIPPING_HOST=0.0.0.0
ENV SHIPPING_PORT=8003
EXPOSE 8003

CMD ["python", "server.py"]
