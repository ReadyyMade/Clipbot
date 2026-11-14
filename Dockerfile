FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# logs aparecem na hora em `docker logs`
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# data/ e secrets/ devem ser volumes para persistir fila, tokens e vídeos
VOLUME ["/app/data", "/app/secrets"]
CMD ["python", "-m", "clipbot", "run", "--interval", "15"]
