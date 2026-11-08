FROM python:3.11.16-slim-bookworm
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium culmus fonts-dejavu-core fonts-liberation fonts-noto-core \
        libasound2 libatk1.0-0 libatk-bridge2.0-0 libcups2 libgbm1 libgomp1 \
        libnss3 libpangocairo-1.0-0 libxcomposite1 libxrandr2 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-semantic.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt -r requirements-semantic.txt \
    && install -d -o 10001 -g 10001 /output \
    && install -d /model
# Mount only the model directory at /model and pass --semantic-model /model.
COPY src/ ./src/
USER 10001:10001
ENV DOCKER_CONTAINER=1 MPLCONFIGDIR=/tmp/matplotlib
ENTRYPOINT ["python", "-m", "src.main"]
CMD ["--output", "/output/report.pdf"]
