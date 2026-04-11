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

Set the **reference point** on the **Dashboard** under **Range and Bearing** (Apply). Use **Settings** for router, **mavlink2rest** URLs, and **poll interval**.

## BlueOS install

1. Enable **API** on the MikroTik (**IP → Services → api**), same subnet as the companion (e.g. `192.168.2.0/24`). This is typically enabled by default.


3. In **Extensions**, use **manual install** . Do **not** leave `permissions` empty — use the value below so port **80**, **`host.docker.internal`**, the **`/data`** bind, and **`NET_RAW`** (ICMP) are applied.

4. Open the extension from the BlueOS sidebar; set **reference coordinates** on the Dashboard and **Settings** (router IP, credentials, mavlink URLs).

### Web UI under BlueOS (no broken CSS / 404 on `/static`)

This app uses:



- **Offline / no CDN:** **Chart.js** is vendored as **`static/vendor/chart.umd.min.js`**.

### Manual install (copy-paste)

Use these values to fill out the manual install UI. The **`permissions`** field is a **string** (escaped JSON) exactly as BlueOS expects when the form shows `"permissions": "{}"` by default — replace that empty object with the string below.


  "identifier": "mikrotik.monitor",
  
  "name": "Mikrotik Monitor",
 
  "docker": "vshie/blueos-mikrotik-monitor",
 
  "tag": "main",
  
  "permissions": see below

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


### mavlink2rest URLs

- **GET (GPS)**: default `http://host.docker.internal/mavlink2rest/mavlink` — used like pingSurvey for  
  `.../vehicles/{id}/components/1/messages/GLOBAL_POSITION_INT`.
- **POST (NamedValueFloat)**: default `http://host.docker.internal:6040/v1/mavlink` — **adjust if your BlueOS build exposes a different port/path** (some setups use the same reverse proxy as GET, e.g. `http://host.docker.internal/mavlink2rest/v1/mavlink`). On the vehicle, confirm the helper schema with:  
  `GET http://<host>:<port>/v1/helper/mavlink?name=NAMED_VALUE_FLOAT`.
- **Payload shape**: mavlink2rest (rust-mavlink) expects `NAMED_VALUE_FLOAT.name` as **an array of 10 single-character JSON strings**, null-padded — not a plain string — and matches the key order used in working BlueOS extensions (`value` then `name`). If you see `HTTP 404 Failed to parse message, not a valid MAVLinkMessage`, the usual cause is the wrong `name` encoding; see [mavlink2rest#52](https://github.com/mavlink/mavlink2rest/issues/52).

### RouterOS credentials

Stored in **`/data/settings.json`** on the volume (**plaintext**). Restrict filesystem access accordingly.

### Browser vs RouterOS login

The **extension web UI** has no login by design (it only shows data from your boat). **MikroTik’s API** authenticates with a RouterOS **username** and **password** (set in **Settings**). “Invalid user name or password” in the dashboard refers to **API `/login` to the radio**, not to the browser.

### How API login works (RouterOS 6.43+)

Official reference: [MikroTik API — protocol and login](https://help.mikrotik.com/docs/spaces/ROS/pages/47579160/API#API-Initiallogin).

After 6.43, login is **not** “send username+password once in plain text” in the way older clients did. The documented flow is a **two-step** `/login`: the router returns a challenge (`=ret=…`), then the client sends `=name=` and `=response=` where **`=response=` is derived from the password** (including an **empty** password). The `routeros-api` library does this when **legacy plaintext** is **off** (extension default).

Standard BlueBoat devices ship with user **`admin`** and **no password**. In that case the password string must be **empty** — using **`admin` as the password** will fail with error 6. In **Settings**, leave **Password** blank; in the test script, omit `-p` and do not set `MIKROTIK_API_PASSWORD` (or set it to an empty string).

The extension and test script **try two API login styles** when the password is empty: **plaintext-style** `/login` with `=name=` and `=password=` (as in the docs), then **challenge-style** if the first fails. Some RouterOS 6.x builds only accept the first for a blank password.



### Troubleshooting: ping works but no SNR / signal fields

1. **API login (RouterOS 6.43+)** — Default is **challenge login** (`router_plaintext_login`: **off**). If login still fails, enable **Legacy plaintext API login** in Settings only for very old RouterOS.
2. **User permissions** — User group must allow **API** login (see above), not only Winbox. Then **read** access is enough to query the registration table.
3. **Empty registration table** — Metrics exist only when the radio is a **station associated to an AP**. If disconnected, the table is empty (dashboard explains this).
4. **wifiwave2** — Default is **off** (BlueBoat / RouterOS 6.x). Enable **Also try wifiwave2** only on **RouterOS 7+** where classic wireless is empty but wifiwave2 has the station.
5. **Dashboard** — After updating, the UI shows **RouterOS / MAVLink diagnostic text** when data is missing; check container logs for the same.

## Local test (Docker Desktop)


## Position note

Position comes from **`GLOBAL_POSITION_INT`** via mavlink2rest; we use **latitude and longitude** only (`/1e7` to decimal degrees). Altitude from that message is **not** read or displayed. The CSV column **`boat_alt_m`** is left **empty** (kept for compatibility with older exports / column layout).
