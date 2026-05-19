FROM python:3.11-slim
RUN pip install --no-cache-dir zeroconf
COPY publish.py /app/publish.py
CMD ["python", "/app/publish.py"]
