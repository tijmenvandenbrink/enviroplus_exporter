ARG ARCH=
FROM ${ARCH}python:3.10.13-slim-bookworm as builder

WORKDIR /enviroplus

RUN ln -fs /usr/share/zoneinfo/Europe/London /etc/localtime

RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y \
    python3 \
    python3-pip

RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y \
    build-essential libssl-dev libffi-dev python3-dev

COPY requirements.txt .

RUN pip3 install -r requirements.txt

FROM ${ARCH}python:3.10.13-slim-bookworm

WORKDIR /enviroplus

# Make sure you update Python version in path
COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages

COPY enviroplus_exporter.py .

CMD python3 enviroplus_exporter.py --bind=0.0.0.0 --port=8000

# ENTRYPOINT [ "python3", "enviroplus_exporter.py", "--bind=0.0.0.0", "--port=8000" ]