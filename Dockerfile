# ── Stage 1: build the React SPA ─────────────────────────────────────────────
FROM node:20-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build   # → /build/dist (Vite base=/app/)

# ── Stage 2: Python runtime ──────────────────────────────────────────────────
# Use the official Python image from the Docker Hub
FROM python:3.12

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . .

# Drop in the built SPA (FastAPI serves it under /app; see web/app.py SPA_DIST)
COPY --from=frontend /build/dist ./frontend/dist

# Update and Upgrade packages
RUN apt -y update && apt -y upgrade

# Install Unrar
RUN apt -y install unrar-free

RUN apt -y install unar

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

RUN pwd

RUN rm -fr logs && rm -fr comics

RUN mkdir logs && mkdir comics

RUN chmod 755 */

RUN ls -althr

EXPOSE 8000

CMD ["python", "entrypoint.py"]
