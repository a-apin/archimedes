FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
ENV PYTHONPATH=/app
CMD ["python", "-m", "archimedes.chain.strategy_runner_publisher"]
