FROM node:20-alpine AS ui
WORKDIR /ui
COPY ui/package.json ./
RUN npm install --silent
COPY ui/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends bash procps && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=ui /ui/dist ./ui/dist
EXPOSE 8000
CMD ["python", "main.py", "serve"]
