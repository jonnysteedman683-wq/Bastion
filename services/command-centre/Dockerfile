# command-centre: build the web app with Node, then serve it with standard-library Python.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm test && npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Run as an unprivileged user with no home directory or login shell.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.txt && rm requirements.txt
COPY app.py roster.json caps.json sources.json ./
COPY --from=web /web/dist ./web/dist

USER 10001
EXPOSE 8080
CMD ["python", "app.py"]
