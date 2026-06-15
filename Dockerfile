FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libopus0 \
    && rm -rf /var/lib/apt/lists/*

# Copy source and requirements
COPY requirements.txt .
COPY src/mumble_recorder ./mumble_recorder

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create recordings directory
RUN mkdir -p /recordings

# Run the spike
ENTRYPOINT ["python", "-m", "mumble_recorder.spike"]
