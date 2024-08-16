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
    && pip install "aced-submission==0.0.9rc29" \
    && pip install "gen3-tracker==0.0.5rc3"

ENV PYTHONUNBUFFERED=1
RUN mkdir ~/.aws ~/.gen3 /root/studies

RUN git clone https://github.com/bmeg/iceberg.git && \
	cd iceberg && \
	git checkout feature/FHIR-resource-type

COPY . /root

#Add jsonschemagraph exe to image
RUN wget https://github.com/bmeg/jsonschemagraph/releases/download/v0.0.1/jsonschemagraph-linux.amd64 -P /usr/local/bin/
RUN mv /usr/local/bin/jsonschemagraph-linux.amd64 /usr/local/bin/jsonschemagraph
RUN chmod +x /usr/local/bin/jsonschemagraph
ENV PATH="/usr/local/bin:$PATH"

EXPOSE 8000
ENTRYPOINT ["uvicorn", "bundle_service.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]