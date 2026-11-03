# hello: standard library only, so no dependency install step.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Run as an unprivileged user with no home directory or login shell.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY app.py .

USER 10001
EXPOSE 8080
CMD ["python", "app.py"]
