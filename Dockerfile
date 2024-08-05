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


SHELL ["/bin/bash", "-c"]

# Create and activate virtual environment, then install dependencies in a single RUN command

COPY ./requirements.txt /root
Run python3 -m venv venv \
    && source venv/bin/activate \
    && pip install wheel yq \
    && pip install -r requirements.txt \
    && pip install aced-submission==0.0.9rc25

ENV PYTHONUNBUFFERED=1
RUN mkdir ~/.aws ~/.gen3 /root/studies

COPY . /root
RUN chmod +x /root/entrypoint.sh

# Download config file
RUN curl https://raw.githubusercontent.com/bmeg/iceberg-schema-tools/development/config.yaml -o config.yaml

# Ensure the working directory is set to /root
WORKDIR /root

ENTRYPOINT ["/root/entrypoint.sh"]
CMD ["uvicorn", "bundle_service.main:app", "--reload"]