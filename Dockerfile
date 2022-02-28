ARG ARCH=
FROM ${ARCH}ubuntu:20.04

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip

COPY requirements.txt .

RUN pip3 install -r requirements.txt

COPY enviroplus_exporter.py .

CMD python3 enviroplus_exporter.py --bind=0.0.0.0 --port=8000
