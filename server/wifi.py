"""WiFi provisioning: status and connection management for wlan1 (USB dongle)."""
import asyncio
import logging

log = logging.getLogger("picar")

_CTL = "/usr/local/sbin/picar-wifi-ctl"


async def _run(*args):
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", _CTL, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode(), err.decode()


async def status():
    code, out, _ = await _run("status")
    info = {"connected": False, "ssid": None, "ip": None}
    if code != 0:
        return info
    for line in out.splitlines():
        key, _, val = line.partition(":")
        val = val.strip()
        if key == "GENERAL.STATE":
            info["connected"] = val.startswith("100")
        elif key == "GENERAL.CONNECTION" and val:
            info["ssid"] = val
        elif key == "IP4.ADDRESS[1]" and val:
            info["ip"] = val.split("/")[0]
    return info


async def scan():
    """Return visible networks sorted by signal strength, strongest first."""
    code, out, _ = await _run("scan")
    if code != 0:
        return []
    seen, networks = set(), []
    for line in out.splitlines():
        # rsplit from the right: SSID (may contain colons), SIGNAL, SECURITY
        parts = line.rsplit(":", 2)
        if len(parts) < 2:
            continue
        ssid = parts[0]
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        try:
            signal = int(parts[1])
        except (ValueError, IndexError):
            signal = 0
        security = parts[2].strip() if len(parts) > 2 else ""
        networks.append({"ssid": ssid, "signal": signal, "secured": bool(security)})
    return sorted(networks, key=lambda n: n["signal"], reverse=True)


async def connect(ssid: str, wifi_pw: str):
    """Connect wlan1 to ssid. Returns (success: bool, message: str)."""
    code, out, err = await _run("connect", ssid, wifi_pw)
    if code == 0:
        log.info("wifi: connected to %r", ssid)
        return True, "connected"
    msg = (err or out).strip().split("\n")[-1]
    log.warning("wifi: connect to %r failed: %s", ssid, msg)
    return False, msg


async def disconnect():
    """Disconnect wlan1 and remove its connection profile."""
    code, _, err = await _run("disconnect")
    if code != 0:
        log.warning("wifi: disconnect failed: %s", err.strip())
    return code == 0
