FROM python:3.12.16-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

RUN useradd --uid 1000 --create-home app && mkdir -p /app/staticfiles && chown app /app/staticfiles
USER app
