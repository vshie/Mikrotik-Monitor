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
2. Ensure the host data directory exists (settings + CSV persist here):

   ```bash
   sudo mkdir -p /usr/blueos/extensions/mikrotik-monitor
   ```

3. In **Extensions**, install from the Bazaar, **or** use **manual install** (paste JSON below). Do **not** leave `permissions` empty — use the value below so port **80**, **`host.docker.internal`**, the **`/data`** bind, and **`NET_RAW`** (ICMP) are applied.

4. Open the extension from the BlueOS sidebar; set **Settings** (router IP, credentials, reference lat/lon, mavlink URLs).

### Manual install (copy-paste)

Use this single JSON object in the manual install UI. The **`permissions`** field is a **string** (escaped JSON) exactly as BlueOS expects when the form shows `"permissions": "{}"` by default — replace that empty object with the string below.

```json
{
  "identifier": "mikrotik.monitor",
  "name": "Mikrotik Monitor",
  "docker": "vshie/blueos-mikrotik-monitor",
  "tag": "main",
  "permissions": "{\"ExposedPorts\":{\"80/tcp\":{}},\"HostConfig\":{\"ExtraHosts\":[\"host.docker.internal:host-gateway\"],\"PortBindings\":{\"80/tcp\":[{\"HostPort\":\"\"}]},\"Binds\":[\"/usr/blueos/extensions/mikrotik-monitor:/data\"],\"CapAdd\":[\"NET_RAW\"]}}"
}
```

Equivalent **`permissions`** value, formatted for reading (must be **stringified** into `permissions` as above if the UI only accepts a string):

```json
{
  "ExposedPorts": {
    "80/tcp": {}
  },
  "HostConfig": {
    "ExtraHosts": ["host.docker.internal:host-gateway"],
    "PortBindings": {
      "80/tcp": [
        {
          "HostPort": ""
        }
      ]
    },
    "Binds": [
      "/usr/blueos/extensions/mikrotik-monitor:/data"
    ],
    "CapAdd": ["NET_RAW"]
  }
}
```

Change **`docker`** / **`tag`** if you use another registry or image tag. **`identifier`** should stay a stable id for the same extension across upgrades.

### mavlink2rest URLs

- **GET (GPS)**: default `http://host.docker.internal/mavlink2rest/mavlink` — used like pingSurvey for  
  `.../vehicles/{id}/components/1/messages/GLOBAL_POSITION_INT`.
- **POST (NamedValueFloat)**: default `http://host.docker.internal:6040/v1/mavlink` — **adjust if your BlueOS build exposes a different port/path**. On the vehicle, confirm with:  
  `GET http://<host>:<port>/v1/helper/mavlink?name=NAMED_VALUE_FLOAT`.

### RouterOS credentials

Stored in **`/data/settings.json`** on the volume (**plaintext**). Restrict filesystem access accordingly.

### Browser vs RouterOS login

The **extension web UI** has no login by design (it only shows data from your boat). **MikroTik’s API** always requires a valid **RouterOS user and password** (set in the extension **Settings**). “Invalid user name or password” in the dashboard refers to **API `/login` to the radio**, not to the browser.

### Test API from your laptop (same network as `192.168.2.4`)

On a computer that can reach the radio (e.g. `ping 192.168.2.4`):

```bash
cd /path/to/this/repo
python3 -m venv .venv && source .venv/bin/activate
pip install routeros-api

# Use the REAL password (Winbox/WebFig), not a README placeholder string.
export MIKROTIK_API_PASSWORD='paste-the-actual-password-here'
python scripts/test_mikrotik_api.py --host 192.168.2.4 --username admin
```

If login fails with **invalid user name or password (6)** but you used a placeholder like `ACTUAL_ROUTER_PASSWORD` in `-p`, RouterOS is literally checking that string — it is not a template.

- **Challenge login** (default, no `--plaintext`) is correct for RouterOS 6.43+; you should see a `/login ... =response=...` step before failure if the API is reachable.
- **Legacy**: add `--plaintext` only if you know the router requires it.
- **API-SSL only**: try `python scripts/test_mikrotik_api.py --host 192.168.2.4 -u admin --ssl --port 8729` (after setting `MIKROTIK_API_PASSWORD`).

When the script prints **Login OK** and lists registration paths, the same **username / password / plaintext / port** behavior applies in the extension **Settings** (plain API is port **8728** unless you only expose SSL).

### `admin` / `admin` works in Winbox but API says error 6

Winbox and the **API use different permission flags**. RouterOS often returns **invalid user name or password (6)** for API when the account is **not allowed to log in via API**, even if the password is correct.

1. **Winbox** → **System** → **Users** → **Groups** → open the group used by `admin` (often `full`).
2. Ensure **API** is allowed (RouterOS 6: **Policies** / policy string must include `api`; RouterOS 7: enable **api** in the group’s login / policy UI).
3. CLI check (SSH to the router): `/user group print detail` — the `policy` line for that group should include **`api`** among the flags (`read`, `write`, `api`, …).

After the group allows API, use **username `admin`**, password **`admin`**, **legacy plaintext off**, port **8728** in the extension Settings (same as a successful test script run).

### Troubleshooting: ping works but no SNR / signal fields

1. **API login (RouterOS 6.43+)** — Default is **challenge login** (`router_plaintext_login`: **off**). If login still fails, enable **Legacy plaintext API login** in Settings only for very old RouterOS.
2. **User permissions** — User group must allow **API** login (see above), not only Winbox. Then **read** access is enough to query the registration table.
3. **Empty registration table** — Metrics exist only when the radio is a **station associated to an AP**. If disconnected, the table is empty (dashboard explains this).
4. **wifiwave2** — On some **RouterOS 7+** builds, link stats live under **`/interface/wifiwave2/registration-table`**; leave **Also try wifiwave2** enabled (default). On pure 6.x, the second path may error in logs; that is harmless.
5. **Dashboard** — After updating, the UI shows **RouterOS / MAVLink diagnostic text** when data is missing; check container logs for the same.

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
