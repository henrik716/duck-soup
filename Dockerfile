FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY duck_soup ./duck_soup
RUN pip install --no-cache-dir .

WORKDIR /data
EXPOSE 8000

CMD ["duck-soup", "serve", "--host", "0.0.0.0", "--port", "8000"]
