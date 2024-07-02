FROM pytorch/torchserve:0.11.0-gpu as builder

WORKDIR /app

RUN sed -i s@/archive.ubuntu.com/@/mirrors.tuna.tsinghua.edu.cn/@g /etc/apt/sources.list && \
    apt-get update -y --allow-unauthenticated --fix-missing && \
    apt-get install -y software-properties-common && \
    apt-get install --no-install-recommends -y ffmpeg rubberband-cli && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* && \
    rm /etc/apt/sources.list.d/cuda.list

COPY requirements.docker.txt ./requirements.docker.txt

USER root

RUN pip install -r requirements.docker.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . ./
