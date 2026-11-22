FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --uid 10001 --create-home broadcaster \
 && install -d -m 0700 -o 10001 -g 10001 /data
COPY broadcaster ./broadcaster
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "broadcaster.web:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
