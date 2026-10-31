FROM python:3.13-slim
WORKDIR /studio
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY CONTRACTS/ ./CONTRACTS/
COPY schema/ ./schema/
# Source-relative frozen schema resolution requires the copied /studio/src tree.
RUN python -m pip install --no-cache-dir -e '.[coordinator]'
ENV PYTHONUNBUFFERED=1
ENV STUDIO_DATA_ROOT=/var/lib/nomad-studio
VOLUME ["/var/lib/nomad-studio"]
CMD ["python", "-m", "studio.app.cli"]
