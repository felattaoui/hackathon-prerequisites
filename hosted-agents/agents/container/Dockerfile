# Hosted agent packaged as a container image (scenario S26: image pulled by the platform from a PRIVATE ACR).
# Same code as the reference minimal agent; pinned lock; linux/amd64; listens on 8088 (hosted-agent contract).
FROM mcr.microsoft.com/azurelinux/base/python:3.12
WORKDIR /app
COPY requirements.txt main.py ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt
EXPOSE 8088
CMD ["python3", "main.py"]
