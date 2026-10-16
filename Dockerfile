FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8793
# keys only as environment variables: docker run -e LLM_BASE_URL=... -e EVOLUS_API_KEY=... -p 8793:8793 vyson-auditor
CMD ["python", "web/servidor.py"]
