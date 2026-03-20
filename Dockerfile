# BlueOS extension: Mikrotik link monitor
FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

EXPOSE 80

ARG IMAGE_NAME=mikrotik-monitor
ARG GITHUB_REPO=Mikrotik-Monitor
ARG AUTHOR=Tony White
ARG AUTHOR_EMAIL=tony@bluerobotics.com
ARG MAINTAINER=Blue Robotics
ARG MAINTAINER_EMAIL=tony@bluerobotics.com
ARG REPO=github.com/vshie/Mikrotik-Monitor
ARG OWNER=vshie

LABEL version="1.2.0"
LABEL permissions='{\
  "ExposedPorts": {\
    "80/tcp": {}\
  },\
  "HostConfig": {\
    "ExtraHosts": ["host.docker.internal:host-gateway"],\
    "PortBindings": {\
      "80/tcp": [{\
        "HostPort": ""\
      }]\
    },\
    "Binds": [\
      "/usr/blueos/extensions/mikrotik-monitor:/data"\
    ],\
    "CapAdd": ["NET_RAW"]\
  }\
}'
LABEL authors="[{\"name\": \"${AUTHOR}\", \"email\": \"${AUTHOR_EMAIL}\"}]"
LABEL company="{\"about\": \"Mikrotik client link metrics, GPS distance, CSV, MAVLink NamedValueFloat\", \"name\": \"${MAINTAINER}\", \"email\": \"${MAINTAINER_EMAIL}\"}"
LABEL readme="https://raw.githubusercontent.com/${OWNER}/${GITHUB_REPO}/main/README.md"
LABEL type="device-integration"
LABEL tags='["communication", "data-collection", "navigation"]'

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
