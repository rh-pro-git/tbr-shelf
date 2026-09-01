FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-noto-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV TBR_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8460

CMD ["tbr-shelf", "--host", "0.0.0.0", "--port", "8460"]
