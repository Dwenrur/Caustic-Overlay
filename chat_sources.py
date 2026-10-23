"""Chat connections for Caustic Overlay: Twitch, Kick and YouTube.

Built-in Python only (no pip installs). Each platform runs on its own
background thread and never touches Tk. Everything it learns is handed to
`emit(event)`, which the overlay drains on the Tk thread. Events are tuples:

    ("chat",   platform, {"id", "username", "message", "user_key", "time"})
    ("delete", platform, message_id)          # a moderator deleted one message
    ("purge",  platform, user_key)            # a user was timed out / banned
    ("clear",  platform, None)                # whole chat cleared
    ("status", platform, (state, detail))     # state: connecting / live / waiting / error / off
    ("kick_chatroom", "kick", (slug, chatroom_id))   # remember a looked-up Kick chatroom ID

How each platform is read:
- Twitch:  official anonymous read-only chat (IRC over WebSocket, "justinfan" login).
- Kick:    Kick's public Pusher WebSocket. Needs the channel's chatroom ID, looked up
           from kick.com (often blocked by Cloudflare for non-browsers, so it can also
           be pasted in by hand).
- YouTube: the same "InnerTube" feed youtube.com's own chat popout uses. No API key.
           Unofficial: if YouTube changes its site, this is the part that can break.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import random
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

# Every connection step is logged here; the overlay writes it to connection_log.txt.
LOG = logging.getLogger("caustic")


# =========================================================================== WebSocket

class WebSocketClosed(Exception):
    def __init__(self, message: str, code: int | None = None, reason: str = ""):
        super().__init__(message)
        self.code = code
        self.reason = reason


class WebSocket:
    """Small RFC 6455 WebSocket client (text frames, ping/pong, close, fragmentation).

    recv(timeout) returns the next text message, or None if nothing arrived in time.
    Partial frames are kept in a buffer, so a timeout never loses data.
    """
    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, url: str, timeout: float = 10.0, headers: dict | None = None,
                 ssl_context: ssl.SSLContext | None = None):
        parts = urllib.parse.urlsplit(url)
        secure = parts.scheme == "wss"
        host = parts.hostname or ""
        port = parts.port or (443 if secure else 80)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        sock = socket.create_connection((host, port), timeout=timeout)
        if secure:
            ctx = ssl_context or ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)
        self.sock = sock
        self._buf = bytearray()
        self._fragments: list[bytes] = []
        self._closed = False
        self._lock = threading.Lock()

        key = base64.b64encode(os.urandom(16)).decode()
        lines = [f"GET {path} HTTP/1.1", f"Host: {host}" + (f":{parts.port}" if parts.port else ""),
                 "Upgrade: websocket", "Connection: Upgrade", f"Sec-WebSocket-Key: {key}",
                 "Sec-WebSocket-Version: 13", f"User-Agent: {BROWSER_UA}"]
        for name, value in (headers or {}).items():
            lines.append(f"{name}: {value}")
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())

        while b"\r\n\r\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WebSocketClosed("connection closed during handshake")
            self._buf += chunk
            if len(self._buf) > 65536:
                raise WebSocketClosed("handshake response too large")
        head, _, rest = bytes(self._buf).partition(b"\r\n\r\n")
        self._buf = bytearray(rest)               # data the server sent right after the handshake
        status_line, *header_lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in f"{status_line} ":
            raise WebSocketClosed(f"handshake refused: {status_line}")
        response_headers = {}
        for line in header_lines:
            name, _, value = line.partition(":")
            response_headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1((key + self.GUID).encode()).digest()).decode()
        if response_headers.get("sec-websocket-accept") != expected:
            raise WebSocketClosed("bad Sec-WebSocket-Accept from server")

    # -- sending
    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += n.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += n.to_bytes(8, "big")
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            self.sock.sendall(bytes(header) + masked)

    def send(self, text: str) -> None:
        if self._closed:
            raise WebSocketClosed("socket is closed")
        self._send_frame(0x1, text.encode("utf-8"))

    # -- receiving
    def _parse_frame(self):
        """Return (fin, opcode, payload) if a whole frame is buffered, else None."""
        buf = self._buf
        if len(buf) < 2:
            return None
        fin = bool(buf[0] & 0x80)
        opcode = buf[0] & 0x0F
        masked = bool(buf[1] & 0x80)
        n = buf[1] & 0x7F
        pos = 2
        if n == 126:
            if len(buf) < 4:
                return None
            n = int.from_bytes(buf[2:4], "big")
            pos = 4
        elif n == 127:
            if len(buf) < 10:
                return None
            n = int.from_bytes(buf[2:10], "big")
            pos = 10
        mask = b""
        if masked:
            if len(buf) < pos + 4:
                return None
            mask = bytes(buf[pos:pos + 4])
            pos += 4
        if len(buf) < pos + n:
            return None
        payload = bytes(buf[pos:pos + n])
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        del buf[:pos + n]
        return fin, opcode, payload

    def recv(self, timeout: float = 1.0) -> str | None:
        deadline = time.monotonic() + timeout
        while True:
            frame = self._parse_frame()
            if frame is not None:
                fin, opcode, payload = frame
                if opcode == 0x8:                      # close (payload: 2-byte code + reason text)
                    self._closed = True
                    try:
                        self._send_frame(0x8, payload[:2])
                    except OSError:
                        pass
                    code = int.from_bytes(payload[:2], "big") if len(payload) >= 2 else None
                    reason = payload[2:].decode("utf-8", errors="replace")
                    text = "server closed the connection"
                    if code is not None:
                        text += f" (code {code}{': ' + reason if reason else ''})"
                    raise WebSocketClosed(text, code, reason)
                if opcode == 0x9:                      # ping -> pong
                    self._send_frame(0xA, payload)
                    continue
                if opcode == 0xA:                      # pong
                    continue
                if opcode in (0x1, 0x2, 0x0):
                    self._fragments.append(payload)
                    if fin:
                        data = b"".join(self._fragments)
                        self._fragments = []
                        if opcode == 0x2 and not data:
                            continue
                        return data.decode("utf-8", errors="replace")
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(65536)
            except (socket.timeout, TimeoutError):
                return None
            except ssl.SSLWantReadError:
                return None
            if not chunk:
                self._closed = True
                raise WebSocketClosed("connection lost")
            self._buf += chunk

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._send_frame(0x8, (1000).to_bytes(2, "big"))
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass


# =========================================================================== base

class ChatSource(threading.Thread):
    """One platform connection. Reconnects with back-off until stop() is called."""
    platform = ""

    def __init__(self, target: str, emit, **options):
        super().__init__(daemon=True, name=f"chat-{self.platform}")
        self.target = (target or "").strip()
        self.emit = emit
        self.options = options
        self._stop_event = threading.Event()
        self._ws: WebSocket | None = None
        self._seen: dict[str, None] = {}
        self._min_retry = 0.0           # a session can ask for a longer wait before the next try

    # helpers ----------------------------------------------------------------
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    def wait(self, seconds: float) -> bool:
        """Sleep, waking early on stop(). Returns True if stopped."""
        return self._stop_event.wait(seconds)

    def stop(self) -> None:
        self._stop_event.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def status(self, state: str, detail: str = "") -> None:
        LOG.info("%s: status %s%s", self.platform, state, f" ({detail})" if detail else "")
        self.emit(("status", self.platform, (state, detail)))

    def log(self, message: str, *args) -> None:
        LOG.info("%s: " + message, self.platform, *args)

    def chat(self, msg_id: str, username: str, message: str, user_key: str = "") -> None:
        msg_id = str(msg_id or "")
        if msg_id:
            if msg_id in self._seen:          # de-duplicate (e.g. Kick on two channels)
                return
            self._seen[msg_id] = None
            if len(self._seen) > 2000:
                for old in list(self._seen)[:1000]:
                    del self._seen[old]
        message = (message or "").strip()
        if not message:
            return
        self.emit(("chat", self.platform, {"id": msg_id, "username": username or "?", "message": message,
                                           "user_key": (user_key or username or "").lower(),
                                           "time": time.strftime("%H:%M")}))

    # main loop --------------------------------------------------------------
    def run(self) -> None:
        delay = 2.0
        while not self.stopped():
            started = time.monotonic()
            try:
                self.session()
            except Exception as exc:          # anything unexpected: show it, retry later
                if self.stopped():
                    break
                LOG.warning("%s: %s: %r", self.platform, exc.__class__.__name__, exc)
                self.status("error", self.describe_error(exc))
            finally:
                if self._ws is not None:
                    try:
                        self._ws.close()
                    except Exception:
                        pass
                    self._ws = None
            if self.stopped():
                break
            delay = 2.0 if time.monotonic() - started > 60 else min(delay * 2, 60.0)
            delay = max(delay, self._min_retry)
            self._min_retry = 0.0
            if self.wait(delay):
                break
        self.status("off")

    def session(self) -> None:          # pragma: no cover - overridden
        raise NotImplementedError

    @staticmethod
    def describe_error(exc: Exception) -> str:
        if isinstance(exc, urllib.error.HTTPError):
            if exc.code in (403, 429):
                return f"site blocked the request (HTTP {exc.code}), retrying"
            return f"HTTP {exc.code}"
        if isinstance(exc, urllib.error.URLError):
            return f"can't reach the site ({exc.reason})"[:80]
        if isinstance(exc, (socket.gaierror,)):
            return "no internet / DNS"
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return "timed out"
        if isinstance(exc, ssl.SSLError):
            return "secure connection failed"
        text = str(exc) or exc.__class__.__name__
        return text[:80]


def http_get(url: str, timeout: float = 15.0, headers: dict | None = None) -> tuple[str, str]:
    """GET a URL like a browser would. Returns (final_url, body_text)."""
    request = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Cookie": "SOCS=CAI; CONSENT=YES+",        # skip YouTube's EU cookie-consent page
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read().decode(charset, errors="replace")
            LOG.info("GET %s -> %s, %d chars%s", _short(url), response.status, len(body),
                     f", ended at {_short(response.geturl())}" if response.geturl() != url else "")
            return response.geturl(), body
    except urllib.error.HTTPError as exc:
        LOG.warning("GET %s -> HTTP %s", _short(url), exc.code)
        raise


def http_post_json(url: str, payload: dict, timeout: float = 15.0, headers: dict | None = None,
                   quiet: bool = False) -> dict:
    data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, method="POST", headers={
        "User-Agent": BROWSER_UA, "Content-Type": "application/json", "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9", "Cookie": "SOCS=CAI; CONSENT=YES+", **(headers or {}),
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            if not quiet:
                LOG.info("POST %s -> %s, %d chars", _short(url), response.status, len(body))
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        LOG.warning("POST %s -> HTTP %s", _short(url), exc.code)
        raise


def _short(url: str) -> str:
    """URL for the log without long tokens/keys."""
    return re.sub(r"(key|continuation)=[^&]+", r"\1=…", url)[:120]


# =========================================================================== Twitch

def normalize_twitch(target: str) -> str:
    t = target.strip()
    m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", t)
    if m:
        t = m.group(1)
    return t.lstrip("#@").lower()


_TAG_UNESCAPE = {r"\s": " ", r"\:": ";", r"\\": "\\", r"\r": "\r", r"\n": "\n"}


def parse_irc(line: str) -> tuple[dict, str, str, list[str]]:
    """Parse one IRC line -> (tags, prefix, command, params). Trailing param keeps its spaces."""
    tags: dict[str, str] = {}
    prefix = ""
    if line.startswith("@"):
        raw_tags, _, line = line[1:].partition(" ")
        for item in raw_tags.split(";"):
            key, _, value = item.partition("=")
            tags[key] = re.sub(r"\\[s:\\rn]", lambda m: _TAG_UNESCAPE[m.group(0)], value)
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    trailing = None
    if " :" in line:
        line, _, trailing = line.partition(" :")
    parts = line.split()
    command = parts[0] if parts else ""
    params = parts[1:]
    if trailing is not None:
        params.append(trailing)
    return tags, prefix, command, params


class TwitchChat(ChatSource):
    platform = "twitch"
    URL = "wss://irc-ws.chat.twitch.tv:443"

    def session(self) -> None:
        channel = normalize_twitch(self.target)
        if not channel:
            self.status("off")
            self._stop_event.wait()
            return
        self.status("connecting")
        self.log("joining #%s", channel)
        ws = self._ws = WebSocket(self.URL, ssl_context=self.options.get("ssl_context"))
        ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
        ws.send("PASS SCHMOOPIIE")          # any password works for anonymous justinfan logins
        ws.send(f"NICK justinfan{random.randint(10000, 99999)}")
        ws.send(f"JOIN #{channel}")
        last_rx = time.monotonic()
        pinged = False
        while not self.stopped():
            text = ws.recv(timeout=1.0)
            now = time.monotonic()
            if text is None:
                if now - last_rx > 90:
                    raise ConnectionError("no data for 90 s, reconnecting")
                if now - last_rx > 60 and not pinged:
                    ws.send("PING :tmi.twitch.tv")
                    pinged = True
                continue
            last_rx, pinged = now, False
            for line in text.split("\r\n"):
                if line:
                    self.handle_line(line, channel)

    def handle_line(self, line: str, channel: str) -> None:
        tags, prefix, command, params = parse_irc(line)
        if command == "PING":
            self._ws.send("PONG :" + (params[-1] if params else "tmi.twitch.tv"))
        elif command in ("ROOMSTATE", "366"):
            self.status("live")
        elif command == "PRIVMSG" and len(params) >= 2:
            text = params[1]
            if text.startswith("\x01ACTION ") and text.endswith("\x01"):
                text = text[8:-1]                       # /me message
            login = prefix.split("!", 1)[0]
            self.chat(tags.get("id", ""), tags.get("display-name") or login, text, login)
        elif command == "CLEARMSG":
            if tags.get("target-msg-id"):
                self.emit(("delete", self.platform, tags["target-msg-id"]))
        elif command == "CLEARCHAT":
            if len(params) >= 2:
                self.emit(("purge", self.platform, params[1].lower()))
            else:
                self.emit(("clear", self.platform, None))
        elif command == "RECONNECT":
            raise ConnectionError("Twitch asked us to reconnect")
        elif command == "NOTICE" and params:
            msg_id = tags.get("msg-id", "")
            if msg_id in ("msg_channel_suspended", "msg_banned") or "unsuccessful" in params[-1].lower():
                self.status("error", params[-1][:80])


# =========================================================================== Kick

def normalize_kick(target: str) -> str:
    t = target.strip()
    m = re.search(r"kick\.com/([A-Za-z0-9_\-]+)", t)
    if m:
        t = m.group(1)
    return t.lstrip("@").lower()


_KICK_EMOTE = re.compile(r"\[emote:\d+:([^\]]+)\]")


def kick_text(content: str) -> str:
    """Kick sends emotes inline as [emote:123:Name]; show the emote's name."""
    return _KICK_EMOTE.sub(lambda m: m.group(1), content or "")


class KickChat(ChatSource):
    platform = "kick"
    # Kick rotated its Pusher key in mid-October 2026 (old one: 32cbd69e4b950bf97679,
    # now refused with "4001 App key not in this cluster"). The current key is asked
    # from Kick on every connect; this is only the fallback.
    PUSHER_KEY = "34bf7a0ff419a2a775b9"
    PUSHER_CLUSTER = "us2"
    PUSHER_URL = "wss://ws-{cluster}.pusher.com/app/{key}?protocol=7&client=js&version=8.4.0-rc2&flash=false"
    REALTIME_API = "https://web.kick.com/api/v1/realtime/connection"   # what kick.com's own site asks
    CHANNEL_API = "https://kick.com/api/v2/channels/{slug}"
    _cached_credentials: tuple[str, str] | None = None     # shared by all Kick connections this run
    _rejected_keys: set = set()                             # keys Kick refused this run (never retried)
    KEY_REJECTED_RETRY = 30.0                               # seconds to wait after Kick refuses a key

    def current_pusher_credentials(self) -> tuple[str, str, str]:
        """(key, cluster, where it came from). Settings override > cached > ask Kick > built-in."""
        override = str(self.options.get("pusher_key") or "").strip()
        if override:
            return override, self.PUSHER_CLUSTER, "Settings"
        if KickChat._cached_credentials:
            return (*KickChat._cached_credentials, "cached")
        try:
            response = http_post_json(self.REALTIME_API, {
                "client": {"id": base64.b16encode(os.urandom(16)).decode().lower(), "type": "web"},
                "capabilities": {"accepted_providers": [{"provider": "pusher"}]},
            }, timeout=10, headers={"Origin": "https://kick.com", "Referer": "https://kick.com/"})
            # Shape: {"data": {"connections": [{"credentials": {"app_key": ..., "cluster": ...}}]}}
            key = find_key(response, "app_key") or find_key(find_key(response, "pusher") or {}, "key")
            cluster = find_key(response, "cluster") or self.PUSHER_CLUSTER
            if isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9]{10,40}", key) and \
                    isinstance(cluster, str) and re.fullmatch(r"[a-z0-9-]{2,12}", cluster) and \
                    key not in KickChat._rejected_keys:
                KickChat._cached_credentials = (key, cluster)
                return key, cluster, "kick.com"
            self.log("realtime API answered without a usable key: %s", json.dumps(response)[:300])
        except Exception as exc:          # blocked / changed: fall back to the built-in key
            self.log("couldn't ask kick.com for the chat key (%r); using built-in key", exc)
        return self.PUSHER_KEY, self.PUSHER_CLUSTER, "built-in"

    def lookup_chatroom(self, slug: str) -> int:
        """Ask kick.com for the chatroom ID. Cloudflare often blocks this for non-browsers."""
        try:
            _, body = http_get(self.CHANNEL_API.format(slug=urllib.parse.quote(slug)),
                               headers={"Accept": "application/json"})
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429, 503):
                raise PermissionError("kick.com blocked the lookup; paste the chatroom ID in Settings → Connections")
            if exc.code == 404:
                raise LookupError(f"Kick channel '{slug}' not found")
            raise
        try:
            data = json.loads(body)
        except ValueError:
            raise PermissionError("kick.com blocked the lookup; paste the chatroom ID in Settings → Connections")
        room = (data.get("chatroom") or {}).get("id")
        if not room:
            raise LookupError(f"Kick channel '{slug}' has no chatroom yet")
        return int(room)

    def session(self) -> None:
        slug = normalize_kick(self.target)
        if not slug:
            self.status("off")
            self._stop_event.wait()
            return
        self.status("connecting")
        room = str(self.options.get("chatroom_id") or "").strip()
        if not room.isdigit():
            try:
                room = str(self.lookup_chatroom(slug))
            except (PermissionError, LookupError) as exc:
                self.status("error", str(exc))
                self.wait(300)            # don't hammer kick.com; user can paste the ID meanwhile
                return
            self.emit(("kick_chatroom", self.platform, (slug, room)))
        self.log("channel %s, chatroom %s", slug, room)
        key, cluster, source = self.current_pusher_credentials()
        self.log("chat key %s… (cluster %s, from %s)", key[:6], cluster, source)
        ws = self._ws = WebSocket(self.PUSHER_URL.format(key=key, cluster=cluster),
                                  ssl_context=self.options.get("ssl_context"), headers={"Origin": "https://kick.com"})
        self._pusher_error = None
        try:
            self.pusher_loop(ws, room, key, source)
        except WebSocketClosed as exc:
            # Kick usually says why in a pusher:error message just before closing.
            if exc.code in (4001, 4003) or (self._pusher_error and self._pusher_error[0] in (4001, 4003)):
                KickChat._cached_credentials = None          # ask Kick for a fresh key next time
                if source != "built-in":
                    KickChat._rejected_keys.add(key)
                self._min_retry = self.KEY_REJECTED_RETRY
                if source == "Settings":
                    raise ConnectionError("Kick rejected the Pusher key in Settings; clear that box")
                raise ConnectionError("Kick rejected its chat key; fetching a fresh one")
            if self._pusher_error:
                raise ConnectionError(f"Kick: {self._pusher_error[1]}"[:90])
            raise

    def pusher_loop(self, ws: WebSocket, room: str, key: str, source: str) -> None:
        channels = (f"chatrooms.{room}.v2", f"chatrooms.{room}")
        activity_timeout = 120.0
        last_rx = time.monotonic()
        ping_sent = 0.0
        while not self.stopped():
            text = ws.recv(timeout=1.0)
            now = time.monotonic()
            if text is None:
                if ping_sent and now - ping_sent > 30:
                    raise ConnectionError("Kick stopped answering, reconnecting")
                if not ping_sent and now - last_rx > activity_timeout:
                    ws.send(json.dumps({"event": "pusher:ping", "data": {}}))
                    ping_sent = now
                continue
            last_rx, ping_sent = now, 0.0
            try:
                packet = json.loads(text)
            except ValueError:
                continue
            event = packet.get("event", "")
            data = packet.get("data")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    pass
            if event not in ("pusher:ping", "pusher:pong") and not event.endswith("ChatMessageEvent"):
                self.log("pusher event %s %s", event, text[:200])
            if event == "pusher:connection_established":
                if isinstance(data, dict) and data.get("activity_timeout"):
                    activity_timeout = float(data["activity_timeout"])
                for channel in channels:
                    ws.send(json.dumps({"event": "pusher:subscribe", "data": {"auth": "", "channel": channel}}))
            elif event == "pusher:ping":
                ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
            elif event == "pusher_internal:subscription_succeeded":
                self.status("live")
            elif event == "pusher:error":
                # Connection-level errors (codes 4000-4299) are followed by the server
                # closing the socket; remember why, so the error shown is the real reason.
                code = data.get("code") if isinstance(data, dict) else None
                detail = (data.get("message") if isinstance(data, dict) else None) or str(data)
                self._pusher_error = (int(code) if str(code).isdigit() else None, f"{detail} (code {code})")
                self.log("pusher error %s: %s", code, detail)
            elif isinstance(data, dict):
                self.handle_event(event, data)

    def handle_event(self, event: str, data: dict) -> None:
        name = event.rsplit("\\", 1)[-1]
        if name in ("ChatMessageEvent", "ChatMessageSentEvent"):
            if "content" in data:                                    # current format
                sender = data.get("sender") or {}
                self.chat(data.get("id", ""), sender.get("username", ""), kick_text(data.get("content", "")),
                          sender.get("slug") or sender.get("username", ""))
            elif isinstance(data.get("message"), dict):              # older format
                message, user = data["message"], data.get("user") or {}
                self.chat(message.get("id", ""), user.get("username", ""), kick_text(message.get("message", "")),
                          user.get("slug") or user.get("username", ""))
        elif name == "MessageDeletedEvent":
            message = data.get("message") or {}
            if message.get("id"):
                self.emit(("delete", self.platform, str(message["id"])))
        elif name == "UserBannedEvent":
            user = data.get("user") or {}
            key = user.get("slug") or user.get("username")
            if key:
                self.emit(("purge", self.platform, key.lower()))
        elif name == "ChatroomClearEvent":
            self.emit(("clear", self.platform, None))


# =========================================================================== YouTube

def find_key(obj, key):
    """Depth-first search for the first value stored under `key` anywhere in nested JSON."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if key in cur:
                return cur[key]
            stack.extend(reversed(list(cur.values())))
        elif isinstance(cur, list):
            stack.extend(reversed(cur))
    return None


def extract_json_after(html: str, marker_regex: str):
    """Find `marker` in the page and decode the JSON object that starts right after it."""
    m = re.search(marker_regex, html)
    if not m:
        return None
    start = html.find("{", m.end())
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[start:])
        return obj
    except ValueError:
        return None


def yt_runs_text(runs) -> str:
    """Turn YouTube 'runs' (text pieces + emoji) into plain text."""
    out = []
    for run in runs or []:
        if "text" in run:
            out.append(run["text"])
        elif "emoji" in run:
            emoji = run["emoji"]
            if emoji.get("isCustomEmoji"):
                shortcuts = emoji.get("shortcuts") or []
                out.append(shortcuts[0] if shortcuts else "")
            else:
                out.append(emoji.get("emojiId") or (emoji.get("shortcuts") or [""])[0])
    return "".join(out)


def yt_text(obj) -> str:
    if not obj:
        return ""
    if "simpleText" in obj:
        return obj["simpleText"]
    return yt_runs_text(obj.get("runs"))


_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_youtube_target(target: str) -> tuple[str, str]:
    """Return ("video", id) or ("channel", url_path) for whatever the user typed."""
    t = target.strip()
    for pattern in (r"[?&]v=([A-Za-z0-9_-]{11})", r"youtu\.be/([A-Za-z0-9_-]{11})",
                    r"youtube\.com/(?:live|shorts|embed)/([A-Za-z0-9_-]{11})"):
        m = re.search(pattern, t)
        if m:
            return "video", m.group(1)
    m = re.search(r"youtube\.com/(@[^/?#]+|channel/[A-Za-z0-9_-]+|c/[^/?#]+|user/[^/?#]+)", t)
    if m:
        return "channel", m.group(1)
    if t.startswith("@"):
        return "channel", t
    if t.startswith("UC") and len(t) == 24:
        return "channel", f"channel/{t}"
    if _VIDEO_ID.match(t) and not t.startswith("@"):
        return "video", t
    return "channel", "@" + t


class YouTubeChat(ChatSource):
    platform = "youtube"
    BASE = "https://www.youtube.com"
    CHECK_LIVE_EVERY = 60.0

    @staticmethod
    def blocked_reason(final_url: str, html: str) -> str | None:
        """Recognise pages YouTube shows instead of the real one."""
        if "consent.youtube.com" in final_url or "consent.google" in final_url:
            return "YouTube showed a cookie-consent page"
        if "confirm you" in html.lower() and "not a bot" in html.lower():
            return "YouTube asked to confirm you're not a bot; try again later"
        if '"status":"LOGIN_REQUIRED"' in html:
            return "YouTube wants a sign-in for this page"
        return None

    def find_live_video(self) -> tuple[str | None, str]:
        """Returns (video_id, reason). video_id is None when there's nothing live to read."""
        kind, value = parse_youtube_target(self.target)
        if kind == "video":
            self.log("using video %s from the link", value)
            return value, ""
        try:
            final_url, html = http_get(f"{self.BASE}/{value}/live")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None, f"no YouTube channel called {value}; check Settings"
            raise
        blocked = self.blocked_reason(final_url, html)
        if blocked:
            self.log("%s (page %s)", blocked, _short(final_url))
            return None, blocked
        video_id = None
        for pattern in (r'<link[^>]*rel="canonical"[^>]*href="[^"]*?watch\?v=([A-Za-z0-9_-]{11})',
                        r'<link[^>]*href="[^"]*?watch\?v=([A-Za-z0-9_-]{11})"[^>]*rel="canonical"',
                        r'<meta[^>]*property="og:url"[^>]*content="[^"]*?watch\?v=([A-Za-z0-9_-]{11})',
                        r'"videoDetails":\{"videoId":"([A-Za-z0-9_-]{11})"'):
            m = re.search(pattern, html)
            if m:
                video_id = m.group(1)
                break
        if not video_id:
            m = re.search(r"watch\?v=([A-Za-z0-9_-]{11})", final_url)
            video_id = m.group(1) if m else None
        live_now = '"isLiveNow":true' in html or re.search(r'"videoDetails":\{[^{}]*"isLive":true', html) is not None
        upcoming = '"isUpcoming":true' in html
        self.log("/%s/live: video=%s live_now=%s upcoming=%s title=%r", value, video_id, live_now, upcoming,
                 (re.search(r"<title>([^<]{0,80})", html) or [None, ""])[1])
        if not video_id:
            return None, "not live yet, checks every minute"
        if live_now:
            return video_id, ""
        if upcoming:
            return None, "stream scheduled but not started yet"
        return None, "not live yet, checks every minute"

    def session(self) -> None:
        if not self.target:
            self.status("off")
            self._stop_event.wait()
            return
        self.status("connecting")
        video_id, reason = self.find_live_video()
        if not video_id:
            self.status("waiting", reason or "not live")
            self.wait(self.CHECK_LIVE_EVERY)
            return
        final_url, html = http_get(f"{self.BASE}/live_chat?is_popout=1&v={video_id}")
        blocked = self.blocked_reason(final_url, html)
        if blocked:
            self.status("waiting", blocked)
            self.wait(self.CHECK_LIVE_EVERY)
            return
        api_key = (re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html) or [None, ""])[1]
        version = (re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html) or [None, "2.20261001.00.00"])[1]
        data = extract_json_after(html, r'(?:window\["ytInitialData"\]|var ytInitialData|ytInitialData)\s*=\s*')
        renderer = find_key(data, "liveChatRenderer") if data else None
        self.log("chat page: initial data=%s, chat renderer=%s, client version=%s, api key=%s",
                 bool(data), bool(renderer), version, "yes" if api_key else "no")
        if not renderer:
            message = find_key(data, "messageRenderer") if data else None
            note = yt_text(message.get("text")) if isinstance(message, dict) else ""
            if note:
                self.log("chat page says: %s", note)
            self.status("waiting", f"chat unavailable: {note}"[:80] if note else "chat is off or not available yet")
            self.wait(self.CHECK_LIVE_EVERY)
            return
        continuation = self.full_chat_continuation(renderer) or self.next_continuation(renderer)[0]
        if not continuation:
            self.status("waiting", "chat is off or not available yet")
            self.wait(self.CHECK_LIVE_EVERY)
            return
        self.status("live")
        polls = 0
        url = f"{self.BASE}/youtubei/v1/live_chat/get_live_chat?prettyPrint=false" + (f"&key={api_key}" if api_key else "")
        while not self.stopped():
            response = http_post_json(url, {
                "context": {"client": {"clientName": "WEB", "clientVersion": version, "hl": "en", "gl": "US"}},
                "continuation": continuation,
            }, quiet=polls > 0)              # log the first poll; the rest would flood the log
            polls += 1
            chat = find_key(response.get("continuationContents") or {}, "liveChatContinuation")
            if not chat:
                self.log("poll %d: no chat in response (keys: %s)", polls, ", ".join(list(response)[:8]))
                self.status("waiting", "stream ended")
                self.wait(self.CHECK_LIVE_EVERY)
                return
            actions = chat.get("actions", []) or []
            if polls == 1:
                self.log("first poll: %d actions", len(actions))
            for action in actions:
                self.handle_action(action)
            continuation, timeout_ms = self.next_continuation(chat)
            if not continuation:
                self.status("waiting", "stream ended")
                self.wait(self.CHECK_LIVE_EVERY)
                return
            if self.wait(min(max(timeout_ms / 1000.0, 1.0), 5.0)):
                return

    @staticmethod
    def full_chat_continuation(renderer: dict) -> str | None:
        """Prefer 'Live chat' (every message) over the default 'Top chat' (filtered)."""
        items = find_key(renderer.get("header") or {}, "subMenuItems") or []
        for item in reversed(items):         # "Live chat" is listed after "Top chat"
            cont = find_key(item, "continuation")
            if isinstance(cont, dict):
                cont = cont.get("continuation") or find_key(cont, "continuation")
            if isinstance(cont, str):
                return cont
        return None

    @staticmethod
    def next_continuation(obj: dict) -> tuple[str | None, int]:
        for entry in obj.get("continuations", []) or []:
            for value in entry.values():
                if isinstance(value, dict) and value.get("continuation"):
                    return value["continuation"], int(value.get("timeoutMs", 5000))
        return None, 5000

    def handle_action(self, action: dict) -> None:
        if "addChatItemAction" in action:
            item = action["addChatItemAction"].get("item", {})
            if "liveChatTextMessageRenderer" in item:
                r = item["liveChatTextMessageRenderer"]
                self.chat(r.get("id", ""), yt_text(r.get("authorName")).lstrip("@") or "?",
                          yt_text(r.get("message")), r.get("authorExternalChannelId", ""))
            elif "liveChatPaidMessageRenderer" in item:
                r = item["liveChatPaidMessageRenderer"]
                amount = yt_text(r.get("purchaseAmountText"))
                text = yt_text(r.get("message"))
                self.chat(r.get("id", ""), yt_text(r.get("authorName")).lstrip("@") or "?",
                          f"💲 {amount}  {text}".strip(), r.get("authorExternalChannelId", ""))
            elif "liveChatPaidStickerRenderer" in item:
                r = item["liveChatPaidStickerRenderer"]
                self.chat(r.get("id", ""), yt_text(r.get("authorName")).lstrip("@") or "?",
                          f"💲 {yt_text(r.get('purchaseAmountText'))}  (Super Sticker)", r.get("authorExternalChannelId", ""))
            elif "liveChatMembershipItemRenderer" in item:
                r = item["liveChatMembershipItemRenderer"]
                text = yt_text(r.get("headerSubtext")) or yt_text(r.get("headerPrimaryText")) or "New member"
                extra = yt_text(r.get("message"))
                self.chat(r.get("id", ""), yt_text(r.get("authorName")).lstrip("@") or "?",
                          f"⭐ {text}" + (f"  {extra}" if extra else ""), r.get("authorExternalChannelId", ""))
        elif "markChatItemAsDeletedAction" in action:
            target = action["markChatItemAsDeletedAction"].get("targetItemId")
            if target:
                self.emit(("delete", self.platform, target))
        elif "removeChatItemAction" in action:
            target = action["removeChatItemAction"].get("targetItemId")
            if target:
                self.emit(("delete", self.platform, target))
        elif "markChatItemsByAuthorAsDeletedAction" in action:
            channel_id = action["markChatItemsByAuthorAsDeletedAction"].get("externalChannelId")
            if channel_id:
                self.emit(("purge", self.platform, channel_id.lower()))


SOURCES = {"twitch": TwitchChat, "kick": KickChat, "youtube": YouTubeChat}
