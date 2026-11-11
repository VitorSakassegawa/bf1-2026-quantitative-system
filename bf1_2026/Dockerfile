FROM python:3.12-slim

WORKDIR /app

# System dependencies (compilers for any wheel that has to build; libpq)
RUN apt-get update && apt-get install -y \
    gcc g++ libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Directories for models and logs
RUN mkdir -p /app/models_storage /app/logs

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
