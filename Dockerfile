# bePm — Production Docker Image
# Build:  docker build -t bepm .
# Run:    docker run -p 48090:48090 -v $(pwd)/.projects:/app/.projects -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY bepm
FROM python:3.13-slim

WORKDIR /app

# Install deps
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Set production mode
ENV BEPM_PRODUCTION=true

WORKDIR /app/backend
EXPOSE 48090

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "48090"]
