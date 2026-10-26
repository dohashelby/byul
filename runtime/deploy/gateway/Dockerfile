# serving-runtime Gateway image for the formal GPU lab (deploy/gpu-lab).
#
# The runtime uses the Python standard library only, so the package is copied
# as source instead of installed: the build needs no network and no build
# backend download. Pin the base image by digest when building:
#
#   docker build -f deploy/gateway/Dockerfile \
#     --build-arg PYTHON_IMAGE=python:3.12-slim@sha256:<digest> \
#     -t serving-gateway:<version> .
#
# Run from runtime/ (the build context is the runtime project root).
# Unlike deploy/lab/Dockerfile this image carries no test assets or probe files.
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/serving-runtime

COPY src/serving_runtime /opt/serving-runtime/serving_runtime
COPY pyproject.toml /tmp/pyproject.toml

# The CLI reads its version from package metadata; write the minimal
# dist-info next to the source (stdlib tomllib, no install step).
# UID 10000 is a project choice: plan appendix I.4 fixes 10001 (engine),
# 10002 (acquire) and 10003 (probe) but does not name a Gateway UID.
RUN python -c "import pathlib, tomllib; v = tomllib.loads(pathlib.Path('/tmp/pyproject.toml').read_text())['project']['version']; d = pathlib.Path('/opt/serving-runtime/serving_runtime-' + v + '.dist-info'); d.mkdir(); (d / 'METADATA').write_text('Metadata-Version: 2.1\\nName: serving-runtime\\nVersion: ' + v + '\\n')" \
    && rm /tmp/pyproject.toml \
    && groupadd --gid 10000 gateway \
    && useradd --uid 10000 --gid gateway --no-create-home --shell /usr/sbin/nologin gateway

USER 10000:10000
ENTRYPOINT ["python", "-m", "serving_runtime"]
CMD ["--help"]
