# syntax=docker/dockerfile:1

FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY templates ./templates
COPY run_server.py ./
COPY temp/.gitkeep ./temp/.gitkeep
ENV HOST=0.0.0.0
ENV PORT=8000
EXPOSE 8000
CMD ["python", "run_server.py"]
