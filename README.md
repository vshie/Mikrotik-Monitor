# BlueOS extension: Mikrotik link monitor

BlueBoat-oriented extension that:

- Probes reachability of the onboard **MikroTik RouterOS v6** client (default `192.168.2.4`, API **8728**).
- Polls **`/interface/wireless/registration-table`** for SNR and signal metrics.
- Reads **GPS** from BlueOS **mavlink2rest** (same URL pattern as [pingSurvey](https://github.com/vshie/pingSurvey)).
- Computes **great-circle distance** from the boat to **user-entered reference coordinates** (decimal degrees).
- Appends all of the above to **`/data/mikrotik_link.csv`** (persistent volume).
- Sends **`NAMED_VALUE_FLOAT`** messages (`MTK_SNR`, `MTK_TXDB`, `MTK_RXDB`, optional `MTK_DISTM`) via **POST** to mavlink2rest so values appear in ArduPilot **.BIN** logs.

## BlueOS install

1. Enable **API** on the MikroTik (**IP → Services → api**), same subnet as the companion (e.g. `192.168.2.0/24`). This is typically enabled by default. 
2. In **Extensions**, add the image (Docker Hub or manual), with a host bind:

   - Recommended: mount host **`/usr/blueos/extensions/mikrotik-monitor`** to container **`/data`** (matches `LABEL permissions` in the `Dockerfile`).

3. Open the extension from the BlueOS sidebar; set **Settings** (router IP, credentials, reference lat/lon, mavlink URLs).

### mavlink2rest URLs

- **GET (GPS)**: default `http://host.docker.internal/mavlink2rest/mavlink` — used like pingSurvey for  
  `.../vehicles/{id}/components/1/messages/GLOBAL_POSITION_INT`.
- **POST (NamedValueFloat)**: default `http://host.docker.internal:6040/v1/mavlink` — **adjust if your BlueOS build exposes a different port/path**. On the vehicle, confirm with:  
  `GET http://<host>:<port>/v1/helper/mavlink?name=NAMED_VALUE_FLOAT`.

### RouterOS credentials

Stored in **`/data/settings.json`** on the volume (**plaintext**). Restrict filesystem access accordingly.

## Local test (Docker Desktop)

```bash
docker compose up --build
```

Browse **http://localhost:8080**. Logs and settings land in **`./data`**.

## Cross-build for Raspberry Pi (local)

BlueOS on Pi often needs **`linux/arm/v7`** (32-bit OS) or **`linux/arm64`** (64-bit Pi OS / Pi 4 default image). From a Mac or PC with Docker Buildx + QEMU (Docker Desktop enables this by default):

**ARMv7 (32-bit, e.g. older Pi OS armhf):**

```bash
docker buildx build --platform linux/arm/v7 \
  -t blueos-mikrotik-monitor:armv7 --load .
```

**ARM64 (Pi 4 with 64-bit OS — common):**

```bash
docker buildx build --platform linux/arm64 \
  -t blueos-mikrotik-monitor:arm64 --load .
```

`--load` loads a **single** platform into the local Docker daemon. To build both without loading: use `--platform linux/arm/v7,linux/arm64` and `--push` to a registry, or `--output type=tar` per platform.

Copy to the Pi (example):

```bash
docker save blueos-mikrotik-monitor:armv7 | ssh pi@blueos.local docker load
```

Dependencies use **plain `uvicorn`** (not `uvicorn[standard]`) so **armv7** does not need to compile `uvloop`/`httptools` inside the slim image.

## GitHub Actions

Workflow **`.github/workflows/build.yml`** uses [Deploy-BlueOS-Extension](https://github.com/BlueOS-community/Deploy-BlueOS-Extension). Add repository secrets:

- `DOCKER_USERNAME`
- `DOCKER_PASSWORD`

Update **`Dockerfile`** `LABEL readme` / build `ARG OWNER` to match your GitHub org and repo after you publish.

## Development

Use **Python 3.12 or 3.13** locally (3.14 may lack `pydantic-core` wheels yet).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATA_DIR=./data
mkdir -p "$DATA_DIR"
uvicorn app.main:app --reload --port 8000
```

Run from the repository root so `app` and `static` resolve correctly.

## Position note

Position comes from **`GLOBAL_POSITION_INT`** via mavlink2rest; we use **latitude and longitude** only (`/1e7` to decimal degrees). Altitude from that message is **not** read or displayed. The CSV column **`boat_alt_m`** is left **empty** (kept for compatibility with older exports / column layout).
