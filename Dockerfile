FROM node:20-alpine AS ui
WORKDIR /ui
COPY ui/package.json ./
RUN npm install --silent
COPY ui/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends bash procps curl ca-certificates && rm -rf /var/lib/apt/lists/*
# Docker CLI only (no daemon). With /var/run/docker.sock mounted, approved fixes can restart containers on the host.
ARG DOCKER_VERSION=27.3.1
RUN arch="$(uname -m)" && curl -fsSL "https://download.docker.com/linux/static/stable/${arch}/docker-${DOCKER_VERSION}.tgz" \
    | tar -xz -C /usr/local/bin --strip-components=1 docker/docker && docker --version
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=ui /ui/dist ./ui/dist
EXPOSE 8000
CMD ["python", "main.py", "serve"]
