FROM python:3.12-slim
WORKDIR /opt/zdx
COPY . .
RUN pip install --no-cache-dir .
RUN useradd --system --home /var/lib/zdx zdx && mkdir -p /var/lib/zdx && chown zdx:zdx /var/lib/zdx
USER zdx
EXPOSE 8765
ENTRYPOINT ["zdx"]
CMD ["--help"]
