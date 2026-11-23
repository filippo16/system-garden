FROM python:3.11-slim

# Evita generazione di file .pyc e forza flush dell'output console
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Installazione dipendenze di sistema minime (se necessarie) e CA certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copia e installazione requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice sorgente
COPY . .

# Utente non-root per massima sicurezza
RUN useradd -m appuser && chown -R appuser /app
USER appuser

CMD ["python", "main.py"]
