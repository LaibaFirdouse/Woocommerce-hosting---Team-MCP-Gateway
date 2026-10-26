FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chmod +x /app/entrypoint.sh

ENV GATEWAY_HOST=0.0.0.0
ENV GATEWAY_PORT=8001
EXPOSE 8001

CMD ["/app/entrypoint.sh"]
