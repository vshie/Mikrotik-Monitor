# BlueOS extension: Mikrotik link monitor

BlueBoat-oriented extension that:

- Probes reachability of the onboard **MikroTik RouterOS v6** client (default `192.168.2.4`, API **8728**).
- Polls **`/interface/wireless/registration-table`** for SNR and signal metrics.
- Reads **GPS** from BlueOS **mavlink2rest** (same URL pattern as [pingSurvey](https://github.com/vshie/pingSurvey)).
- Computes **great-circle distance** and **true compass bearing** from your **reference point** to the **BlueBoat** (WGS84; bearing is clockwise from north, 0–360°).
- Appends all of the above to **`/data/mikrotik_link.csv`** (persistent volume), including column **`bearing_deg`** when a fix and reference are available.
- Sends **`NAMED_VALUE_FLOAT`** messages (`MTK_SNR`, `MTK_TXDB`, `MTK_RXDB`, and when enabled `MTK_DISTM` + **`MTK_BRNG`**) via **POST** to mavlink2rest so values appear in ArduPilot **.BIN** logs.

### Bazaar store icon

To list this extension in the BlueOS Extensions store, open a PR against [**BlueOS-Extensions-Repository**](https://github.com/bluerobotics/BlueOS-Extensions-Repository) with **`metadata.json`** plus an icon file, as described under [Submission to the Bazaar](https://blueos.cloud/docs/latest/development/extensions/#submission-to-the-bazaar). Use the packaged artwork: **`static/extension-icon.png`** (same graphic as the in-app header).

### Locked target (BlueBoat / single radio)

This extension is **shipped for one on-board profile** (validated on hardware):

| Item | Default |
|------|---------|
| Radio | MikroTik **RouterOS 6.x**, classic **`/interface/wireless/registration-table`** |
| IP | **`192.168.2.4`**, API port **8728** |
| Credentials | User **`admin`**, **empty password** (WebFig default); API uses plaintext-style `/login` first, then challenge if needed |
| wifiwave2 | **Disabled** (RouterOS 7+ path not used on this radio) |

Change **Settings** for per-vehicle items: **reference coordinates**, **mavlink2rest** URLs, **poll interval**. Other router toggles remain for rare overrides only.

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
- **POST (NamedValueFloat)**: default `http://host.docker.internal:6040/v1/mavlink` — **adjust if your BlueOS build exposes a different port/path** (some setups use the same reverse proxy as GET, e.g. `http://host.docker.internal/mavlink2rest/v1/mavlink`). On the vehicle, confirm the helper schema with:  
  `GET http://<host>:<port>/v1/helper/mavlink?name=NAMED_VALUE_FLOAT`.
- **Payload shape**: mavlink2rest (rust-mavlink) expects `NAMED_VALUE_FLOAT.name` as **an array of 10 single-character JSON strings**, null-padded — not a plain string. If you see `HTTP 404 Failed to parse message, not a valid MAVLinkMessage`, the usual cause is the wrong `name` encoding; this extension formats that correctly. See [mavlink2rest#52](https://github.com/mavlink/mavlink2rest/issues/52).

### RouterOS credentials

Stored in **`/data/settings.json`** on the volume (**plaintext**). Restrict filesystem access accordingly.

### Browser vs RouterOS login

The **extension web UI** has no login by design (it only shows data from your boat). **MikroTik’s API** authenticates with a RouterOS **username** and **password** (set in **Settings**). “Invalid user name or password” in the dashboard refers to **API `/login` to the radio**, not to the browser.

### How API login works (RouterOS 6.43+)

Official reference: [MikroTik API — protocol and login](https://help.mikrotik.com/docs/spaces/ROS/pages/47579160/API#API-Initiallogin).

After 6.43, login is **not** “send username+password once in plain text” in the way older clients did. The documented flow is a **two-step** `/login`: the router returns a challenge (`=ret=…`), then the client sends `=name=` and `=response=` where **`=response=` is derived from the password** (including an **empty** password). The `routeros-api` library does this when **legacy plaintext** is **off** (extension default).

Many devices still ship with user **`admin`** and **no password**. In that case the password string must be **empty** — using **`admin` as the password** will fail with error 6. In **Settings**, leave **Password** blank; in the test script, omit `-p` and do not set `MIKROTIK_API_PASSWORD` (or set it to an empty string).

The extension and test script **try two API login styles** when the password is empty: **plaintext-style** `/login` with `=name=` and `=password=` (as in the docs), then **challenge-style** if the first fails. Some RouterOS 6.x builds only accept the first for a blank password.

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

### Groups already list `api` in the policy, but login still fails

The **Groups** screen only shows what each group *can* do. Check the **user** and **service** side:

1. **System → Users → Users** — Open **`admin`**. Confirm **Group** is **`full`** (or **`read`** / **`write`**, which also include `api` per default groups). A **custom** group might omit `api` even if `full` has it.
2. **Allowed address** on that user — Must include the client IP (e.g. `192.168.2.0/24`) or **`0.0.0.0/0`**. If it’s too narrow, API login can fail with a misleading message.
3. **IP → Services → api** — Service **enabled**, port **8728**, and **Available From** (or address list) must allow the **same** subnet as your test PC / BlueOS host (not only Winbox’s subnet).
4. Confirm **`192.168.2.4`** is the same device you’re viewing in WebFig (not a different radio).

Optional isolation: add a user **`api-test`** in group **`read`**, password **`test123`**, allowed address **`0.0.0.0/0`**, then run `scripts/test_mikrotik_api.py` with that user.

### Troubleshooting: ping works but no SNR / signal fields

1. **API login (RouterOS 6.43+)** — Default is **challenge login** (`router_plaintext_login`: **off**). If login still fails, enable **Legacy plaintext API login** in Settings only for very old RouterOS.
2. **User permissions** — User group must allow **API** login (see above), not only Winbox. Then **read** access is enough to query the registration table.
3. **Empty registration table** — Metrics exist only when the radio is a **station associated to an AP**. If disconnected, the table is empty (dashboard explains this).
4. **wifiwave2** — Default is **off** (BlueBoat / RouterOS 6.x). Enable **Also try wifiwave2** only on **RouterOS 7+** where classic wireless is empty but wifiwave2 has the station.
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
