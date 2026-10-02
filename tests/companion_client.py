"""Client HTTP e SSE minimi per i test della companion web.

Le richieste girano in un thread separato mentre il thread principale fa
girare il ciclo eventi Qt (`wait_until`): il server inoltra le chiamate al
thread principale e senza ciclo eventi non risponderebbe.
"""
import http.client
import json
import socket
import threading

from olladesk import companion

PORT = 0
_wait = None


def setup(port: int, wait_until) -> None:
    """Porta del server e funzione che attende facendo girare Qt."""
    global PORT, _wait
    PORT, _wait = port, wait_until


def request(method, path, body=None, headers=None, cookie=None):
    h = {"Host": f"127.0.0.1:{PORT}"}
    if body is not None:
        h["Content-Type"] = "application/json"
        body = json.dumps(body).encode()
    if cookie:
        h["Cookie"] = f"{companion.COOKIE}={cookie}"
    h.update(headers or {})
    out = {}

    def run():
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        out["r"] = (r.status, dict(r.getheaders()), r.read())
        c.close()

    t = threading.Thread(target=run)
    t.start()
    assert _wait(lambda: not t.is_alive(), 15000), f"{method} {path} senza risposta"
    return out["r"]


def get_json(path, cookie):
    st, _h, raw = request("GET", path, cookie=cookie)
    return st, json.loads(raw.decode() or "{}")


def post_json(path, body, cookie):
    st, _h, raw = request("POST", path, body, cookie=cookie)
    return st, json.loads(raw.decode() or "{}")


def pair(auth) -> str:
    """Abbina un dispositivo e restituisce il suo token di sessione."""
    code, _ = auth.new_code()
    st, hd, _b = request("POST", "/api/pair", {"code": code})
    assert st == 200, st
    return hd["Set-Cookie"].split(";")[0].split("=", 1)[1]


class SSE:
    """Client SSE in un thread: raccoglie (id, evento, dati)."""

    def __init__(self, path, cookie, headers=None):
        self.events: list[tuple] = []
        self.status = None
        self.ended = False
        self._sock = None
        port = PORT

        def run():
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            c.request("GET", path, headers={
                "Host": f"127.0.0.1:{port}", "Cookie": f"{companion.COOKIE}={cookie}",
                **(headers or {})})
            r = c.getresponse()
            self.status = r.status
            self._sock = c.sock
            ev_id, kind, data = None, None, []
            try:
                while True:
                    line = r.fp.readline()
                    if not line:
                        break
                    line = line.decode().rstrip("\n")
                    if line.startswith("id: "):
                        ev_id = int(line[4:])
                    elif line.startswith("event: "):
                        kind = line[7:]
                    elif line.startswith("data: "):
                        data.append(line[6:])
                    elif line == "" and kind:
                        self.events.append((ev_id, kind, json.loads("\n".join(data))))
                        ev_id, kind, data = None, None, []
            except OSError:
                pass
            self.ended = True

        threading.Thread(target=run, daemon=True).start()

    def kinds(self):
        return [e[1] for e in self.events]

    def of(self, kind):
        return [e[2] for e in self.events if e[1] == kind]

    def close(self):
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
