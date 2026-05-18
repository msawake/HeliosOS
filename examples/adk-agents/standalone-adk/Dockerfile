FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    google-adk>=1.0.0 \
    google-cloud-aiplatform>=1.93.0 \
    fastapi>=0.115 \
    uvicorn[standard]>=0.34 \
    pydantic-settings>=2.8.1 \
    tabulate>=0.9.0

# Copy the ADK customer-service agent code
COPY adk-samples/python/agents/customer-service/customer_service/ /app/customer_service/

# Copy FastAPI wrapper (placed at build root to avoid .dockerignore)
COPY standalone-adk-main.py /app/main.py

ENV PYTHONPATH=/app
ENV PORT=8080
ENV AGENT_DIR=/app

EXPOSE 8080
CMD ["python", "main.py"]
