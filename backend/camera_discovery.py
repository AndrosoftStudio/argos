import base64
import concurrent.futures
import datetime as _dt
import hashlib
import html
import ipaddress
import os
import re
import socket
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET

import psutil
import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

try:
    requests.packages.urllib3.disable_warnings()
except Exception:
    pass


CAMERA_PORTS = (80, 443, 554, 8554, 8000, 8080, 8081, 8899, 5000, 5001)
RTSP_PORTS = (554, 8554)
HTTP_PORTS = (80, 443, 8000, 8080, 8081, 8899, 5000, 5001)
CAMERA_HINT_RE = re.compile(
    r"(camera|ipcam|ipc|onvif|rtsp|hikvision|dahua|intelbras|axis|"
    r"icsee|xmeye|xm|yoosee|gwell|network video|nvr|dvr)",
    re.I,
)


def discover_cameras(
    subnet=None,
    username="",
    password="",
    timeout=0.35,
    max_workers=96,
    include_onvif=True,
):
    started = time.time()
    timeout = max(0.15, min(float(timeout or 0.35), 2.5))
    username = str(username or "").strip()
    password = str(password or "")
    networks = _requested_networks(subnet) if subnet else _local_networks()
    devices = {}

    if include_onvif:
        for item in _ws_discovery(timeout=max(1.0, min(timeout * 5, 3.0))):
            ip = item.get("ip")
            if not ip:
                continue
            rec = devices.setdefault(ip, _blank_device(ip))
            _merge_device(rec, item)
            rec["discovery"].append("onvif")

    scanned = _scan_networks(networks, timeout=timeout, max_workers=max_workers)
    for ip, scan in scanned.items():
        rec = devices.setdefault(ip, _blank_device(ip))
        rec["open_ports"] = scan.get("open_ports", [])
        if scan.get("http"):
            rec["http"] = scan["http"]
        if _looks_like_camera(rec):
            rec["discovery"].append("scan")
        elif not rec.get("xaddrs"):
            devices.pop(ip, None)

    if include_onvif:
        _collect_onvif_streams(devices, username=username, password=password, timeout=max(1.5, min(timeout * 6, 5.0)))

    for rec in devices.values():
        _add_rtsp_guesses(rec, username=username, password=password)
        rec["discovery"] = sorted(set(rec.get("discovery") or []))
        rec["streams"] = _dedupe_streams(rec.get("streams") or [])
        rec["stream_count"] = len(rec["streams"])
        rec["display_name"] = _display_name(rec)
        rec["notes"] = _notes_for_device(rec)

    result = sorted(
        devices.values(),
        key=lambda d: (
            0 if "onvif" in d.get("discovery", []) else 1,
            d.get("ip", ""),
        ),
    )
    return {
        "networks": [str(n) for n in networks],
        "devices": result,
        "count": len(result),
        "elapsed_sec": round(time.time() - started, 2),
    }


def list_local_webcams(limit=10):
    import cv2

    cams = []
    for i in range(int(limit or 10)):
        cap = cv2.VideoCapture(i)
        try:
            if cap.isOpened():
                cams.append({"index": i, "label": f"Webcam {i}"})
        finally:
            cap.release()
    return cams


def _blank_device(ip):
    return {
        "ip": ip,
        "display_name": f"Camera {ip}",
        "brand": "",
        "model": "",
        "name": "",
        "location": "",
        "hardware": "",
        "xaddrs": [],
        "scopes": [],
        "types": [],
        "open_ports": [],
        "http": None,
        "streams": [],
        "discovery": [],
    }


def _merge_device(rec, item):
    for key in ("xaddrs", "scopes", "types"):
        rec[key] = sorted(set((rec.get(key) or []) + (item.get(key) or [])))
    for key in ("brand", "model", "name", "location", "hardware"):
        if item.get(key) and not rec.get(key):
            rec[key] = item[key]


MAX_REDES_POR_BUSCA = 4  # cada rede ja e limitada a /24; sem teto, uma lista longa varria milhares de IPs


def _requested_networks(value):
    networks = []
    for part in str(value or "").split(",")[:MAX_REDES_POR_BUSCA]:
        raw = part.strip()
        if not raw:
            continue
        try:
            net = ipaddress.ip_network(raw, strict=False)
            if isinstance(net, ipaddress.IPv4Network):
                capped = _cap_network(net)
                if _allowed_network(capped):
                    networks.append(capped)
        except ValueError:
            try:
                ip = ipaddress.ip_address(raw)
                if isinstance(ip, ipaddress.IPv4Address) and (ip.is_private or ip.is_loopback):
                    networks.append(ipaddress.ip_network(f"{ip}/32", strict=False))
            except ValueError:
                pass
    return _dedupe_networks(networks)


def _local_networks():
    networks = []
    try:
        for addrs in psutil.net_if_addrs().values():
            for addr in addrs:
                if addr.family != socket.AF_INET:
                    continue
                ip = addr.address
                if not ip or ip.startswith(("127.", "169.254.")):
                    continue
                netmask = addr.netmask or "255.255.255.0"
                try:
                    net = ipaddress.IPv4Interface(f"{ip}/{netmask}").network
                    networks.append(_cap_network(net, ip=ip))
                except ValueError:
                    networks.append(ipaddress.ip_network(f"{ip}/24", strict=False))
    except Exception:
        pass
    if not networks:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            networks.append(ipaddress.ip_network(f"{ip}/24", strict=False))
        except Exception:
            networks.append(ipaddress.ip_network("127.0.0.1/32", strict=False))
    return _dedupe_networks(networks)


def _cap_network(net, ip=None):
    if net.prefixlen >= 24 or net.num_addresses <= 512:
        return net
    if ip:
        return ipaddress.ip_network(f"{ip}/24", strict=False)
    first = next(net.hosts(), net.network_address)
    return ipaddress.ip_network(f"{first}/24", strict=False)


def _allowed_network(net):
    return bool(net.is_private or net.is_loopback)


def _dedupe_networks(networks):
    out = []
    seen = set()
    for net in networks:
        key = str(net)
        if key not in seen:
            seen.add(key)
            out.append(net)
    return out


def _scan_networks(networks, timeout, max_workers):
    targets = []
    for net in networks:
        if net.prefixlen == 32:
            targets.append(str(net.network_address))
        else:
            targets.extend(str(ip) for ip in net.hosts())
    targets = sorted(set(targets), key=lambda x: tuple(int(p) for p in x.split(".")))
    results = {}
    workers = max(8, min(int(max_workers or 96), 160))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        future_map = {ex.submit(_scan_ip, ip, timeout): ip for ip in targets}
        for fut in concurrent.futures.as_completed(future_map):
            ip = future_map[fut]
            try:
                res = fut.result()
            except Exception:
                continue
            if res.get("open_ports") or res.get("http"):
                results[ip] = res
    return results


def _scan_ip(ip, timeout):
    open_ports = []
    for port in CAMERA_PORTS:
        if _tcp_open(ip, port, timeout):
            open_ports.append(port)
    http = None
    for port in open_ports:
        if port in HTTP_PORTS:
            http = _probe_http(ip, port, timeout=max(timeout, 0.5))
            if http and http.get("camera_hint"):
                break
    return {"ip": ip, "open_ports": open_ports, "http": http}


def _tcp_open(ip, port, timeout):
    try:
        with socket.create_connection((ip, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _probe_http(ip, port, timeout):
    scheme = "https" if int(port) == 443 else "http"
    url = f"{scheme}://{ip}:{port}/"
    try:
        r = requests.get(url, timeout=timeout, verify=False, allow_redirects=True)
        text = r.text[:4096] if r.text else ""
        title = ""
        m = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
        server = r.headers.get("Server", "")
        combined = f"{title} {server} {text[:512]}"
        return {
            "url": url,
            "status": r.status_code,
            "title": title,
            "server": server,
            "camera_hint": bool(CAMERA_HINT_RE.search(combined)),
        }
    except Exception:
        return None


def _looks_like_camera(rec):
    ports = set(rec.get("open_ports") or [])
    if ports.intersection(RTSP_PORTS):
        return True
    if rec.get("xaddrs"):
        return True
    http_info = rec.get("http") or {}
    if http_info.get("camera_hint"):
        return True
    if ports.intersection((8000, 8899)):
        return True
    return False


def _ws_discovery(timeout=2.0):
    msg_id = uuid.uuid4()
    probe = f"""<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>uuid:{msg_id}</w:MessageID>
    <w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe>
  </e:Body>
</e:Envelope>""".encode("utf-8")
    found = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(timeout)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.sendto(probe, ("239.255.255.250", 3702))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                break
            item = _parse_ws_response(data, addr[0])
            ip = item.get("ip")
            if ip:
                rec = found.setdefault(ip, _blank_device(ip))
                _merge_device(rec, item)
    except Exception:
        pass
    finally:
        sock.close()
    return list(found.values())


def _parse_ws_response(data, fallback_ip):
    text = data.decode("utf-8", "ignore")
    item = _blank_device(fallback_ip)
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return item

    xaddrs = []
    scopes = []
    types = []
    for el in root.iter():
        name = _local_name(el.tag)
        if name == "XAddrs" and el.text:
            xaddrs.extend(el.text.split())
        elif name == "Scopes" and el.text:
            scopes.extend(el.text.split())
        elif name == "Types" and el.text:
            types.extend(el.text.split())
    ip = _ip_from_urls(xaddrs) or fallback_ip
    item["ip"] = ip
    item["xaddrs"] = sorted(set(xaddrs))
    item["scopes"] = sorted(set(scopes))
    item["types"] = sorted(set(types))
    item.update(_scope_hints(scopes))
    return item


def _local_name(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _ip_from_urls(urls):
    for url in urls:
        try:
            host = urllib.parse.urlsplit(url).hostname
            if host:
                return host
        except Exception:
            pass
    return ""


def _scope_hints(scopes):
    hints = {}
    for scope in scopes or []:
        decoded = urllib.parse.unquote(str(scope))
        low = decoded.lower()
        for key in ("name", "hardware", "location", "model", "manufacturer"):
            marker = f"/{key}/"
            if marker in low and not hints.get("brand" if key == "manufacturer" else key):
                value = decoded[low.index(marker) + len(marker) :]
                value = value.replace("_", " ").replace("%20", " ").strip("/")
                if key == "manufacturer":
                    hints["brand"] = value
                else:
                    hints[key] = value
    return hints


def _collect_onvif_streams(devices, username, password, timeout):
    targets = [d for d in devices.values() if d.get("xaddrs") or d.get("open_ports")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(24, max(4, len(targets) or 4))) as ex:
        future_map = {
            ex.submit(_fetch_onvif_streams, d, username, password, timeout): d.get("ip")
            for d in targets
        }
        for fut in concurrent.futures.as_completed(future_map):
            ip = future_map[fut]
            try:
                streams, hints = fut.result()
            except Exception:
                continue
            rec = devices.get(ip)
            if not rec:
                continue
            rec["streams"].extend(streams)
            if hints:
                _merge_device(rec, hints)
                if streams and "onvif_media" not in rec["discovery"]:
                    rec["discovery"].append("onvif_media")


def _fetch_onvif_streams(device, username, password, timeout):
    service_url = _device_service_url(device)
    if not service_url:
        return [], {}
    hints = {"ip": device.get("ip"), "xaddrs": [service_url], "scopes": [], "types": []}
    caps_body = """<tds:GetCapabilities xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><tds:Category>All</tds:Category></tds:GetCapabilities>"""
    caps_xml = _soap_call(service_url, caps_body, username, password, timeout)
    if not caps_xml:
        return [], hints
    media_url = _first_text_by_local(caps_xml, "XAddr", contains="/media")
    if not media_url:
        media_url = _guess_media_url(service_url)
    profiles_body = """<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>"""
    profiles_xml = _soap_call(media_url, profiles_body, username, password, timeout)
    if not profiles_xml:
        return [], hints
    profiles = _parse_profiles(profiles_xml)
    streams = []
    for idx, profile in enumerate(profiles, start=1):
        token = profile.get("token")
        if not token:
            continue
        uri_body = f"""<trt:GetStreamUri xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <trt:StreamSetup>
    <tt:Stream>RTP-Unicast</tt:Stream>
    <tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>
  </trt:StreamSetup>
  <trt:ProfileToken>{html.escape(token)}</trt:ProfileToken>
</trt:GetStreamUri>"""
        uri_xml = _soap_call(media_url, uri_body, username, password, timeout)
        uri = _first_text_by_local(uri_xml, "Uri") if uri_xml else ""
        if uri:
            uri = _with_rtsp_credentials(uri, username, password)
            streams.append(
                {
                    "label": profile.get("name") or f"ONVIF perfil {idx}",
                    "uri": uri,
                    "masked_uri": _mask_uri(uri),
                    "source": "onvif",
                    "profile_token": token,
                    "resolution": profile.get("resolution"),
                    "encoding": profile.get("encoding"),
                    "channel": _guess_channel(profile.get("name") or token),
                    "verified": True,
                }
            )
    return streams, hints


def _device_service_url(device):
    for url in device.get("xaddrs") or []:
        if "onvif" in url.lower() and "device" in url.lower():
            return url
    for url in device.get("xaddrs") or []:
        if "onvif" in url.lower():
            return url
    ip = device.get("ip")
    ports = device.get("open_ports") or []
    for port in (80, 8080, 8000, 8899):
        if port in ports:
            return f"http://{ip}:{port}/onvif/device_service"
    if ip:
        return f"http://{ip}/onvif/device_service"
    return ""


def _guess_media_url(device_url):
    parts = urllib.parse.urlsplit(device_url)
    path = parts.path or "/onvif/device_service"
    if "device_service" in path:
        path = path.rsplit("/", 1)[0] + "/media_service"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _soap_call(url, body, username, password, timeout):
    envelope = _soap_envelope(body, username=username, password=password)
    headers = {"Content-Type": "application/soap+xml; charset=utf-8"}
    auths = [None]
    if username:
        auths = [HTTPDigestAuth(username, password), HTTPBasicAuth(username, password), None]
    for auth in auths:
        try:
            r = requests.post(url, data=envelope.encode("utf-8"), headers=headers, timeout=timeout, auth=auth, verify=False)
            if r.status_code < 400 and r.text:
                return r.text
        except Exception:
            continue
    return ""


def _soap_envelope(body, username="", password=""):
    security = ""
    if username:
        nonce = os.urandom(16)
        created = _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        digest = base64.b64encode(hashlib.sha1(nonce + created.encode("utf-8") + password.encode("utf-8")).digest()).decode("ascii")
        nonce_b64 = base64.b64encode(nonce).decode("ascii")
        security = f"""<s:Header>
  <wsse:Security s:mustUnderstand="1" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
    <wsse:UsernameToken>
      <wsse:Username>{html.escape(username)}</wsse:Username>
      <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>
      <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</wsse:Nonce>
      <wsu:Created>{created}</wsu:Created>
    </wsse:UsernameToken>
  </wsse:Security>
</s:Header>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
{security}
<s:Body>{body}</s:Body>
</s:Envelope>"""


def _first_text_by_local(xml_text, local_name, contains=None):
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return ""
    for el in root.iter():
        if _local_name(el.tag) != local_name:
            continue
        text = (el.text or "").strip()
        if not text:
            continue
        if contains and contains.lower() not in text.lower():
            continue
        return text
    return ""


def _parse_profiles(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []
    profiles = []
    for el in root.iter():
        if _local_name(el.tag) != "Profiles":
            continue
        rec = {"token": el.attrib.get("token", "")}
        for child in el.iter():
            name = _local_name(child.tag)
            if name == "Name" and child.text and not rec.get("name"):
                rec["name"] = child.text.strip()
            elif name == "Encoding" and child.text and not rec.get("encoding"):
                rec["encoding"] = child.text.strip()
            elif name == "Resolution":
                width = ""
                height = ""
                for rchild in child:
                    lname = _local_name(rchild.tag)
                    if lname == "Width":
                        width = (rchild.text or "").strip()
                    elif lname == "Height":
                        height = (rchild.text or "").strip()
                if width and height:
                    rec["resolution"] = f"{width}x{height}"
        profiles.append(rec)
    return profiles


def _add_rtsp_guesses(device, username, password):
    ports = [p for p in device.get("open_ports") or [] if p in RTSP_PORTS]
    if not ports:
        return
    for port in ports:
        device["streams"].extend(_rtsp_guess_streams(device["ip"], port, username, password))


def _rtsp_guess_streams(ip, port, username, password):
    auth = ""
    if username:
        auth = urllib.parse.quote(username, safe="") + ":" + urllib.parse.quote(password or "", safe="") + "@"
    host = f"{ip}" if int(port) == 554 else f"{ip}:{port}"
    base = f"rtsp://{auth}{host}"
    common = [
        ("Canal 1 principal - Hikvision/ONVIF", "/Streaming/Channels/101", 1),
        ("Canal 1 secundario - Hikvision/ONVIF", "/Streaming/Channels/102", 1),
        ("Canal 2 principal - dupla lente", "/Streaming/Channels/201", 2),
        ("Canal 2 secundario - dupla lente", "/Streaming/Channels/202", 2),
        ("Canal 1 principal - Dahua/Intelbras", "/cam/realmonitor?channel=1&subtype=0", 1),
        ("Canal 1 secundario - Dahua/Intelbras", "/cam/realmonitor?channel=1&subtype=1", 1),
        ("Canal 2 principal - dupla lente", "/cam/realmonitor?channel=2&subtype=0", 2),
        ("Canal 2 secundario - dupla lente", "/cam/realmonitor?channel=2&subtype=1", 2),
        ("Canal 1 principal - H264", "/h264/ch1/main/av_stream", 1),
        ("Canal 1 secundario - H264", "/h264/ch1/sub/av_stream", 1),
        ("Canal 2 principal - H264", "/h264/ch2/main/av_stream", 2),
        ("Canal 2 secundario - H264", "/h264/ch2/sub/av_stream", 2),
        ("Stream 1", "/stream1", 1),
        ("Stream 2", "/stream2", 2),
        ("Perfil 1", "/profile1/media.smp", 1),
        ("Perfil 2", "/profile2/media.smp", 2),
        ("Live 0 principal", "/live/ch00_0", 1),
        ("Live 0 secundario", "/live/ch00_1", 1),
        ("Live 1 principal", "/live/ch01_0", 2),
        ("Live 1 secundario", "/live/ch01_1", 2),
        ("Live SDP", "/live.sdp", 1),
    ]
    streams = []
    for label, path, channel in common:
        uri = base + path
        streams.append(
            {
                "label": label,
                "uri": uri,
                "masked_uri": _mask_uri(uri),
                "source": "rtsp_guess",
                "channel": channel,
                "verified": False,
            }
        )
    if username:
        u = urllib.parse.quote(username, safe="")
        p = urllib.parse.quote(password or "", safe="")
        for channel in (1, 2):
            for stream in (0, 1):
                path = f"/user={u}&password={p}&channel={channel}&stream={stream}.sdp?real_stream"
                uri = base + path
                streams.append(
                    {
                        "label": f"ICSee/XM canal {channel} stream {stream}",
                        "uri": uri,
                        "masked_uri": _mask_uri(uri),
                        "source": "rtsp_guess",
                        "channel": channel,
                        "verified": False,
                    }
                )
    return streams


def _with_rtsp_credentials(uri, username, password):
    if not username or not uri.lower().startswith("rtsp://"):
        return uri
    try:
        parts = urllib.parse.urlsplit(uri)
        if parts.username:
            return uri
        host = parts.hostname or ""
        if not host:
            return uri
        netloc = urllib.parse.quote(username, safe="") + ":" + urllib.parse.quote(password or "", safe="") + "@"
        netloc += host
        if parts.port:
            netloc += f":{parts.port}"
        return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return uri


def _mask_uri(uri):
    try:
        parts = urllib.parse.urlsplit(uri)
        if not parts.username:
            return uri
        host = parts.hostname or ""
        netloc = urllib.parse.unquote(parts.username) + ":***@" + host
        if parts.port:
            netloc += f":{parts.port}"
        return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return uri


def _dedupe_streams(streams):
    out = []
    seen = set()
    for stream in streams:
        uri = stream.get("uri") or ""
        key = uri.lower()
        if not uri or key in seen:
            continue
        seen.add(key)
        out.append(stream)
    return out


def _display_name(device):
    parts = [device.get("brand"), device.get("model") or device.get("name") or device.get("hardware")]
    label = " ".join(p for p in parts if p).strip()
    if label:
        return f"{label} ({device.get('ip')})"
    http_info = device.get("http") or {}
    if http_info.get("title"):
        return f"{http_info['title']} ({device.get('ip')})"
    return f"Camera {device.get('ip')}"


def _notes_for_device(device):
    notes = []
    onvif_count = len([s for s in device.get("streams") or [] if s.get("source") == "onvif"])
    channels = sorted(set(s.get("channel") for s in device.get("streams") or [] if s.get("channel")))
    if onvif_count >= 2:
        notes.append("ONVIF retornou multiplos perfis de video.")
    if len(channels) >= 2:
        notes.append("Inclui candidatos para canal 1 e canal 2, comum em cameras dupla lente/PTZ + fixa.")
    if any(s.get("source") == "rtsp_guess" for s in device.get("streams") or []):
        notes.append("URLs RTSP sugeridas; use usuario/senha corretos se a camera exigir autenticacao.")
    return notes


def _guess_channel(text):
    m = re.search(r"(?:channel|canal|ch)\s*[_-]?(\d+)", str(text or ""), re.I)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None
