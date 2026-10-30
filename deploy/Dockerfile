FROM python:3.13-slim
WORKDIR /studio
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir '.[coordinator]'
ENV PYTHONUNBUFFERED=1
ENV STUDIO_DATA_ROOT=/var/lib/nomad-studio
VOLUME ["/var/lib/nomad-studio"]
CMD ["python", "-m", "studio.app.cli"]
