FROM python:3.11-slim

WORKDIR /app

# 1. Ensure Python prints logs immediately without buffering
ENV PYTHONUNBUFFERED=1

# 2. Constrain C++ / ONNX thread pools to prevent 64-core OOM crashes in 512MB RAM
ENV OMP_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
ENV OPENBLAS_NUM_THREADS=1
ENV ORT_INTRA_OP_NUM_THREADS=1
ENV ORT_INTER_OP_NUM_THREADS=1
ENV TOKENIZERS_PARALLELISM=false

# 3. Install OS dependencies
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 4. Install Python dependencies
COPY requirements.txt .
RUN pip install -v --no-cache-dir -r requirements.txt

# 5. Copy application code
COPY . .

EXPOSE 8000

# 6. Start Uvicorn constrained to 1 worker to stay safely inside free-tier memory
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]