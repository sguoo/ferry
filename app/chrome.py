"""Sign in with the user's real Chrome (or Edge) on a Ferry-owned profile, then export the session as yt-dlp's
cookies.txt.

    session = chrome.start_login()            # opens the Google sign-in page in a plain browser window
    session.poll()                            # from a timer: None while waiting, count once cookies.txt is written
    session.cancel()                          # closes whatever is open

Two phases, because Google refuses to sign in to any browser that has remote debugging enabled:
1. a completely ordinary Chrome window (no flags Google can see) on the dedicated profile; we watch its window
   title, and once it lands on a YouTube page we close it cleanly (WM_CLOSE) so the profile flushes its cookies;
2. a headless Chrome on the same profile with DevTools on, which hands us the decrypted cookies
   (Storage.getCookies) - the only way past Chrome 127+'s app-bound cookie encryption that broke
   yt-dlp's --cookies-from-browser chrome.
The profile is used for nothing else, so YouTube's cookie rotation never fights a browser the user keeps using.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import shutil
import socket
import struct
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

from .theme import DATA_DIR

COOKIES_PATH = DATA_DIR / "cookies.txt"
PROFILE_DIR = DATA_DIR / "browser-profile"
# after sign-in Google forwards to a YouTube *subpage*: its window title is "<page> - YouTube", whereas the
# sign-in page itself is titled just "YouTube" - that suffix is how we notice the user is done
LOGIN_URL = "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2Ffeed%2Fsubscriptions"
SESSION_COOKIES = ("SAPISID", "__Secure-3PAPISID", "LOGIN_INFO")
KEEP_DOMAINS = ("youtube.com", "google.com")
WM_CLOSE = 0x0010

_APP_PATHS = ("chrome.exe", "msedge.exe")
_FALLBACKS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
_COMMON = ("--no-first-run", "--no-default-browser-check", "--disable-sync")


class LoginError(Exception):
    pass


def find_browser() -> Path | None:
    """Chrome first, then Edge (always present on Windows 10/11)."""
    try:
        import winreg

        for exe in _APP_PATHS:
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                        p = Path(winreg.QueryValue(k, None))
                        if p.is_file():
                            return p
                except OSError:
                    continue
    except ImportError:
        pass
    for cand in _FALLBACKS:
        p = Path(os.path.expandvars(cand))
        if p.is_file():
            return p
    return None


def browser_label(exe: Path | None) -> str:
    return "Chrome" if exe and exe.name.lower() == "chrome.exe" else "Edge" if exe else "브라우저"


def is_logged_in() -> bool:
    return COOKIES_PATH.is_file()


def logout() -> None:
    COOKIES_PATH.unlink(missing_ok=True)
    shutil.rmtree(PROFILE_DIR, ignore_errors=True)


# --------------------------------------------------------------------------- #
# login session
# --------------------------------------------------------------------------- #


class LoginSession:
    def __init__(self, exe: Path):
        self.exe = exe
        self.phase = "signin"  # -> "closing" -> "extract"
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._port_file = PROFILE_DIR / "DevToolsActivePort"
        self._port_file.unlink(missing_ok=True)
        self.proc = subprocess.Popen(
            [str(exe), f"--user-data-dir={PROFILE_DIR}", *_COMMON, "--window-size=560,800", "--new-window", LOGIN_URL], close_fds=True
        )
        self.headless: subprocess.Popen | None = None
        self._landed: float | None = None
        self._phase_at = time.monotonic()

    def poll(self) -> int | None:
        """None while the user is still signing in; the exported cookie count once the session is saved.
        Raises LoginError when it cannot succeed any more."""
        if self.phase == "signin":
            if self.proc.poll() is not None:
                self._start_extract()  # the user closed the window themselves, maybe after signing in
            elif self._on_youtube():
                self._landed = self._landed or time.monotonic()
                if time.monotonic() - self._landed >= 3:  # let YouTube finish setting cookies, then close cleanly
                    _close_windows(self.proc.pid)
                    self.phase, self._phase_at = "closing", time.monotonic()
            return None
        if self.phase == "closing":
            if self.proc.poll() is not None:
                self._start_extract()
            elif time.monotonic() - self._phase_at > 10:
                self.proc.terminate()
            return None
        return self._finish_extract()

    def cancel(self) -> None:
        for proc in (self.proc, self.headless):
            if proc is None or proc.poll() is not None:
                continue
            _close_windows(proc.pid)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    # ---- phases
    def _on_youtube(self) -> bool:
        return any(" - YouTube" in t for _, t in _windows_of(self.proc.pid))

    def _start_extract(self) -> None:
        self._port_file.unlink(missing_ok=True)
        self.headless = subprocess.Popen(
            [str(self.exe), f"--user-data-dir={PROFILE_DIR}", *_COMMON, "--headless=new", "--disable-gpu", "--remote-debugging-port=0", "about:blank"],
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.phase, self._phase_at = "extract", time.monotonic()

    def _finish_extract(self) -> int | None:
        try:
            port, path = self._port_file.read_text(encoding="utf-8").split()[:2]
        except (OSError, ValueError):
            if self.headless.poll() is not None or time.monotonic() - self._phase_at > 20:
                raise LoginError("브라우저에서 쿠키를 읽어오지 못했습니다. 다시 시도해 주세요.")
            return None
        try:
            with _DevTools(f"ws://127.0.0.1:{port}{path}") as dt:
                cookies = dt.call("Storage.getCookies").get("cookies", [])
                dt.call("Browser.close")
        except OSError as exc:
            raise LoginError(f"브라우저와 통신하지 못했습니다: {exc}") from exc
        try:
            self.headless.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.headless.kill()
        if not _has_session(cookies):
            raise LoginError("로그인이 완료되지 않았습니다. 다시 시도해서 Google 로그인을 끝까지 진행해 주세요.")
        return write_netscape(cookies, COOKIES_PATH)


def start_login() -> LoginSession:
    exe = find_browser()
    if exe is None:
        raise LoginError("Chrome 또는 Edge를 찾을 수 없습니다. 둘 중 하나를 설치하면 앱에서 로그인할 수 있습니다.")
    return LoginSession(exe)


def _windows_of(pid: int) -> list[tuple[int, str]]:
    """Visible top-level windows owned by a process, with their titles."""
    user32 = ctypes.windll.user32
    out: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            out.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return out


def _close_windows(pid: int) -> None:
    """Ask the browser to quit the way the user's X button would, so it flushes its profile."""
    for hwnd, _ in _windows_of(pid):
        ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def _has_session(cookies: list[dict]) -> bool:
    return any(c.get("name") in SESSION_COOKIES and "youtube.com" in c.get("domain", "") for c in cookies)


def write_netscape(cookies: list[dict], path: Path) -> int:
    """DevTools cookie dicts -> Netscape cookies.txt (what yt-dlp reads). Returns the count kept."""
    lines = ["# Netscape HTTP Cookie File", "# https://curl.haxx.se/rfc/cookie_spec.html", "# Written by Ferry", ""]
    kept = 0
    for c in cookies:
        domain = c.get("domain", "")
        if not any(domain.endswith(d) for d in KEEP_DOMAINS):
            continue
        expires = c.get("expires", -1)
        expiry = 0 if c.get("session") or not expires or expires < 0 else int(expires)
        prefix = "#HttpOnly_" if c.get("httpOnly") else ""
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        lines.append("\t".join([prefix + domain, include_sub, c.get("path", "/"), "TRUE" if c.get("secure") else "FALSE", str(expiry), c["name"], c.get("value", "")]))
        kept += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return kept


# --------------------------------------------------------------------------- #
# just enough WebSocket for the DevTools browser endpoint (stdlib only)
# --------------------------------------------------------------------------- #


class _DevTools:
    def __init__(self, url: str, timeout: float = 5.0):
        _, rest = url.split("://", 1)
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port)), timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("devtools: connection closed during handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise OSError(f"devtools: handshake refused: {head[:80]!r}")
        self._buf = head.split(b"\r\n\r\n", 1)[1]
        self._id = 0

    def __enter__(self) -> "_DevTools":
        return self

    def __exit__(self, *_exc) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        self._send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self._recv_text())
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise OSError(f"devtools: {method}: {msg['error']}")
                return msg.get("result", {})

    def _send(self, text: str) -> None:
        payload = text.encode()
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x81])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(head + mask + masked)

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise OSError("devtools: connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _recv_text(self) -> str:
        parts: list[bytes] = []
        while True:
            b0, b1 = self._read(2)
            fin, opcode = b0 & 0x80, b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else b""
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:
                raise OSError("devtools: connection closed by browser")
            if opcode == 0x9:  # ping -> pong
                self.sock.sendall(bytes([0x8A, 0x80]) + b"\x00\x00\x00\x00")
                continue
            if opcode in (0x1, 0x0):
                parts.append(data)
                if fin:
                    return b"".join(parts).decode("utf-8")
