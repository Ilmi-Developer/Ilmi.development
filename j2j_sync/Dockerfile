# The Journey 2 Jannah reader's own image (Playwright with Chromium), separate from the Ilmi web service's Dockerfile.
# Railway builds it with the repository as the build context and this file as the service's Dockerfile path.
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble
WORKDIR /app
RUN pip install --no-cache-dir playwright==1.49.0
COPY j2j_sync/ ./j2j_sync/
ENV PYTHONUNBUFFERED=1
CMD ["python", "j2j_sync/explore.py"]
