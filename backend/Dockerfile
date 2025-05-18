# Use Python 3.9 slim image as base
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies required for pandas and numerical libraries
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    libc-dev \
    linux-headers-generic \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install dependencies with specific pip commands
RUN pip install --no-cache-dir wheel setuptools
RUN pip install --no-cache-dir numpy==1.24.3
RUN pip install --no-cache-dir pandas==1.5.3
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose port 8000
EXPOSE 8000

# Set Python path
ENV PYTHONPATH=/app

# Command to run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"] 