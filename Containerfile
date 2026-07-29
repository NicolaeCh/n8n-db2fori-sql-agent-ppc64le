FROM registry.access.redhat.com/ubi9/python-311:latest AS wheel-builder

USER 0
WORKDIR /opt/app-root/src

# python-gssapi has no Linux ppc64le wheel. Build it from source with the
# MIT Kerberos and Python 3.11 development headers, then copy only wheels
# into the final runtime image.
RUN set -eux; \
    if command -v dnf >/dev/null 2>&1; then PM=dnf; else PM=microdnf; fi; \
    ${PM} -y install gcc krb5-devel python3.11-devel; \
    ${PM} clean all; \
    rm -rf /var/cache/dnf /var/cache/yum

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && python -m pip wheel --no-cache-dir --wheel-dir /tmp/wheels -r requirements.txt


FROM registry.access.redhat.com/ubi9/python-311:latest

ARG APP_VERSION=1.0.1
ENV APP_VERSION=${APP_VERSION} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    WEB_CONCURRENCY=1

USER 0
WORKDIR /opt/app-root/src

# krb5-libs supplies the runtime GSSAPI shared libraries needed by the
# compiled Python gssapi extension. Compiler and header packages remain only
# in the wheel-builder stage.
RUN set -eux; \
    if command -v dnf >/dev/null 2>&1; then PM=dnf; else PM=microdnf; fi; \
    ${PM} -y install krb5-libs; \
    ${PM} clean all; \
    rm -rf /var/cache/dnf /var/cache/yum

COPY requirements.txt ./requirements.txt
COPY --from=wheel-builder /tmp/wheels /tmp/wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/tmp/wheels -r requirements.txt \
    && python -c "import gssapi; from mapepire_python import SQLJob; print('Mapepire and gssapi imports verified')" \
    && rm -rf /tmp/wheels

COPY app ./app
RUN chown -R 1001:0 /opt/app-root/src \
    && chmod -R g=u /opt/app-root/src

USER 1001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=3)" || exit 1
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers ${WEB_CONCURRENCY:-1}"]
