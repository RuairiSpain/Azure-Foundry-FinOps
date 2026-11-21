# Build from the suite root so the image contains this commit's SDK. To use the released package from PyPI instead,
# delete the two SDK lines below; requirements.txt already lists foundry-finops:
#   docker build -f foundry-finops-cowork/Dockerfile -t foundry-finops-mcp .
# Once foundry-finops is published you can drop the two SDK lines and rely on requirements.txt only.
FROM python:3.12-slim
WORKDIR /app
COPY foundry-finops /sdk
RUN pip install --no-cache-dir /sdk
COPY foundry-finops-cowork/mcp_server/requirements.txt .
RUN grep -v '^foundry-finops' requirements.txt > /tmp/req.txt && pip install --no-cache-dir -r /tmp/req.txt
COPY foundry-finops-cowork/mcp_server ./mcp_server
ENV HOST=0.0.0.0 PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
USER 1000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=3)"
CMD ["python", "-m", "mcp_server.app"]
