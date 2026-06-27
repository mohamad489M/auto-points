FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    chromium \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Copy backend
COPY backend/ ./backend/

# Copy frontend  ← هاد المهم!
COPY frontend/ ./frontend/

# Install Python dependencies
RUN pip install --no-cache-dir -r backend/requirements.txt

# Install Playwright
RUN playwright install chromium

# Expose port
EXPOSE 8000

# Start command
CMD ["sh", "-c", "cd backend && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
