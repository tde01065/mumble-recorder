FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libopus0 \
    && rm -rf /var/lib/apt/lists/*

# Copy source and requirements
COPY requirements.txt .
COPY setup.py .
COPY src ./src
COPY tests ./tests

# Install Python dependencies and package
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -e .

# Create recordings directory
RUN mkdir -p /recordings

# Import smoke test to verify no AttributeError on pymumble types
RUN python -c "import mumble_recorder.mumble_client; print('mumble_client import ok')"

# Run the recorder core
ENTRYPOINT ["python", "-m", "mumble_recorder"]
