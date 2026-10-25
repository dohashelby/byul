FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/serving-runtime
COPY . .

RUN python -m pip install --no-cache-dir --no-deps . \
    && groupadd --gid 10001 serving \
    && useradd --uid 10001 --gid serving --no-create-home --shell /usr/sbin/nologin serving \
    && mkdir -p /opt/serving/assets/model /opt/serving/cache /opt/serving/secret /opt/serving/probe \
    && printf '%s\n' 'test-only model asset' > /opt/serving/assets/model/model.txt \
    && printf '%s\n' 'test-only placeholder' > /opt/serving/secret/read-probe.txt \
    && : > /opt/serving/probe/blocked-probe.txt \
    && chown -R serving:serving /opt/serving

USER 10001:10001
