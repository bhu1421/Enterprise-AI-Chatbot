
FROM python:3.12-slim 

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/home/atlas/.cache/huggingface

WORKDIR /srv

# Required by some ML/PyTorch dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt && \
    useradd -r -m -U -u 10001 atlas && \
    mkdir -p /home/atlas/.cache/huggingface && \
    chown -R atlas:atlas /home/atlas

COPY app ./app
COPY worker ./worker

USER atlas

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
