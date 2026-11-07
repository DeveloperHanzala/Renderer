# Free renderer: turns the post data from n8n into a PNG or GIF in Hanzala's style.
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -c "import playwright" || pip install --no-cache-dir --break-system-packages playwright==1.56.0
WORKDIR /app
COPY server.py .
ENV PORT=8080
EXPOSE 8080
CMD ["python3", "server.py"]
