# nqq — zero third-party dependencies, so the image is tiny and reproducible.
FROM python:3.11-slim
WORKDIR /app
COPY . /app
ENV NQQ_HOST=0.0.0.0 NQQ_PORT=8900 NQQ_HOME=/data
VOLUME /data
EXPOSE 8900
# readiness: the /api/ready endpoint returns 200 once the store + ledger are up
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8900/api/ready',timeout=3).status==200 else 1)"
CMD ["python3", "api/server.py"]
