FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    INFERENCE_BACKEND=speciesnet \
    ANIMAL_DETECTOR=megadetector \
    PORT=9000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY requirements-model-runtime.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt
RUN pip install --no-cache-dir --prefer-binary -r requirements-model-runtime.txt

COPY app ./app
COPY config ./config
COPY models ./models

EXPOSE 9000

CMD ["python", "-m", "app"]
