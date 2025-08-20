FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /app/data

COPY app/ /app/
COPY prompt_fernanda.md .
COPY clinica_config.json .
COPY knowledge_base.csv .

EXPOSE 8000

CMD ["uvicorn", "fernanda_backend:app", "--host", "0.0.0.0", "--port", "8000"]  