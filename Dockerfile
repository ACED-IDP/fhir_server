# set base image (host OS)
FROM python:3.12-bookworm

# install dependencies
RUN apt-get -y update \
    && apt-get -y --no-install-recommends install \
           build-essential \
           curl \
           gcc \
           git \
           libmagic-dev \
           libpq-dev \
           python3-dev \
           unzip \
           vim \
           automake \
           libtool \
           wget \
           ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# set the working directory in the container
WORKDIR /root

RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" \
    && unzip awscliv2.zip \
    && ./aws/install \
    && rm awscliv2.zip


COPY ./requirements.txt /root
Run pip install wheel yq \
    && pip install -r requirements.txt \
    && pip install aced-submission==0.0.9rc25

ENV PYTHONUNBUFFERED=1
RUN mkdir ~/.aws ~/.gen3 /root/studies

COPY . /root

# Download config file
RUN curl https://raw.githubusercontent.com/bmeg/iceberg-schema-tools/development/config.yaml -o config.yaml

# Ensure the working directory is set to /root
WORKDIR /root

EXPOSE 8000
ENTRYPOINT ["uvicorn", "bundle_service.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]