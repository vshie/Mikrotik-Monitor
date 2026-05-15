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
ARG AUTHOR="Tony White"
ARG AUTHOR_EMAIL=tony@bluerobotics.com
ARG MAINTAINER="Blue Robotics"
ARG MAINTAINER_EMAIL=support@bluerobotics.com
ARG REPO=github.com/vshie/Mikrotik-Monitor
ARG OWNER=vshie

# Unprefixed labels are read by the BlueOS-Extensions-Repository scraper.
# org.blueos.* duplicates are kept for backward compatibility with anything
# that still expects the legacy prefix.
LABEL version="1.3.0"
LABEL type="device-integration"
LABEL requirements="core >= 1.1"
LABEL tags='["communication", "data-collection", "navigation"]'

LABEL org.blueos.version="1.3.0"
LABEL org.blueos.type="device-integration"
LABEL org.blueos.requirements="core >= 1.1"
LABEL org.blueos.tags='["communication", "data-collection", "navigation"]'

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
LABEL org.blueos.permissions='{\
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

LABEL authors='[{"name": "Tony White", "email": "tony@bluerobotics.com"}]'
LABEL org.blueos.authors='[{"name": "Tony White", "email": "tony@bluerobotics.com"}]'

LABEL company='{\
    "about": "Mikrotik client link metrics, GPS distance, CSV, MAVLink NamedValueFloat",\
    "name": "Blue Robotics",\
    "email": "support@bluerobotics.com"\
}'
LABEL org.blueos.company='{\
    "about": "Mikrotik client link metrics, GPS distance, CSV, MAVLink NamedValueFloat",\
    "name": "Blue Robotics",\
    "email": "support@bluerobotics.com"\
}'

LABEL readme='https://raw.githubusercontent.com/vshie/Mikrotik-Monitor/{tag}/README.md'
LABEL org.blueos.readme='https://raw.githubusercontent.com/vshie/Mikrotik-Monitor/{tag}/README.md'

LABEL links='{\
    "source": "https://github.com/vshie/Mikrotik-Monitor",\
    "website": "https://bluerobotics.com"\
}'
LABEL org.blueos.links='{\
    "source": "https://github.com/vshie/Mikrotik-Monitor",\
    "website": "https://bluerobotics.com"\
}'

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
