# The verifier gets its own container: a browser for rendering the drawing,
# which the agent never had. The image ships the browsers, not the package.
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble
RUN pip install --no-cache-dir --break-system-packages playwright==1.64.1
COPY test.sh check.py /tests/
