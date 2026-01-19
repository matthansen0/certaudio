# Dockerfile for CertAudio content generation
# Used by Azure Container Instance jobs for long-running generation

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for Azure Speech SDK
RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    ca-certificates \
    libasound2 \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY src/pipeline/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy pipeline code
COPY src/pipeline/ .

# Set working directory to pipeline root (for relative imports)
WORKDIR /app

# Default command - can be overridden via ACI command-line
CMD ["python3", "-m", "tools.generate_all", "--help"]
