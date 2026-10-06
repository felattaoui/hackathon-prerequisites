# Test MCP tool for the hosted-agent lab. Built remotely by ACR Tasks (no local Docker needed).
# The azurelinux python image provides python3 only (no "python" executable): observed 2026-10-02.
FROM mcr.microsoft.com/azurelinux/base/python:3.12
WORKDIR /app
COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt
COPY auth.py app.py ./
RUN useradd --uid 10001 app || adduser -u 10001 -D app || true
USER 10001
EXPOSE 8000
CMD ["python3", "app.py"]
