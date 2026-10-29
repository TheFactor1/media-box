"""Secrets at rest, pairing words, rate limits and the public-bind check. Standard library only."""
import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
import socket
import threading
import time

WORDS = [w for w in open(os.path.join(os.path.dirname(__file__), 'words.txt'), encoding='utf-8').read().split('\n')
         if w and not w.startswith('#')]
assert len(WORDS) == 2048, 'words.txt must hold 2048 words'


def secret_file(path, mode=0o600):
    """32 random bytes, created once with `mode` and kept from then on."""
    if os.path.exists(path):
        os.chmod(path, mode)
        with open(path, 'rb') as f:
            data = f.read()
        if len(data) != 32:  # never silently replace: a new key would make the stored settings unreadable
            raise SystemExit(f'{path}: expected 32 bytes; restore it from backup or delete it to start over')
        return data
    return write_secret(path, secrets.token_bytes(32), mode)


def write_secret(path, data, mode=0o600):
    tmp = path + '.tmp'
    try:  # a leftover (or planted) tmp keeps its own mode under O_CREAT, so start from nothing and O_EXCL
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    if hasattr(os, 'fchmod'):
        os.fchmod(fd, mode)  # explicit: the process umask (077) would otherwise strip the token's group bit
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())  # a crash must not leave an empty key: the settings would be unreadable
    os.replace(tmp, path)
    return data


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def pairing_words(n=6):
    # 6 words from 2048 = 66 bits: a one-time code exchanged for the token, too short to be the token itself.
    return [secrets.choice(WORDS) for _ in range(n)]


# shortcut: stdlib stream cipher (HMAC-SHA256 keystream + HMAC-SHA256 tag), swap for AES-GCM if a crypto dep is ever added
def _subkeys(key):
    return hmac.digest(key, b'hub enc', 'sha256'), hmac.digest(key, b'hub mac', 'sha256')


def _keystream(key, nonce, n):
    return b''.join(hmac.digest(key, nonce + i.to_bytes(8, 'big'), 'sha256') for i in range((n + 31) // 32))[:n]


def _bound(name):
    # The tag covers the row's name, so a value copied into another row fails its check. NUL-terminated: no
    # name + nonce split can be read as another name's.
    return b'' if name is None else name.encode() + b'\0'


def encrypt(key, plaintext, name):
    enc, mac = _subkeys(key)
    nonce = secrets.token_bytes(16)
    ct = bytes(a ^ b for a, b in zip(plaintext, _keystream(enc, nonce, len(plaintext))))
    return nonce + ct + hmac.digest(mac, _bound(name) + nonce + ct, 'sha256')


def decrypt(key, blob, name=None):
    """name=None checks the old, unbound form; only Hub.seal_old_settings (one-time, at startup) asks for it."""
    enc, mac = _subkeys(key)
    nonce, ct, tag = blob[:16], blob[16:-32], blob[-32:]
    if len(blob) < 48 or not hmac.compare_digest(tag, hmac.digest(mac, _bound(name) + nonce + ct, 'sha256')):
        raise ValueError('setting failed its integrity check (tampered, or encrypted with another key)')
    return bytes(a ^ b for a, b in zip(ct, _keystream(enc, nonce, len(ct))))


class Bucket:
    """Token bucket per client key: `burst` requests at once, refilled at `rate` per second."""

    def __init__(self, rate, burst):
        self.rate, self.burst, self.state, self.lock = rate, burst, {}, threading.Lock()

    def take(self, key):
        # shortcut: one entry per client IP, never pruned; fine on a LAN, prune idle keys if the hub is ever exposed
        with self.lock:
            now = time.monotonic()
            tokens, at = self.state.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - at) * self.rate)
            ok = tokens >= 1
            self.state[key] = (tokens - 1 if ok else tokens, now)
            return ok


def route_source():
    """This host's source address on the default route. A UDP connect only picks a route; no packet is sent."""
    # shortcut: sees the default-route interface only; a public address on another interface still needs getifaddrs
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(('192.0.2.1', 1))
            return s.getsockname()[0]
        except OSError:
            return None


def public_addresses(bind):
    """The globally routable IPv4 addresses `bind` would listen on (0.0.0.0 means every address of this host)."""
    host = socket.gethostname() if bind in ('', '0.0.0.0') else bind
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(host, None, socket.AF_INET)}
    except OSError:  # an unresolvable bind fails at listen time anyway
        addrs = set()
    if bind in ('', '0.0.0.0'):  # the hostname often resolves to 127.0.1.1 only, so also ask the default route
        addrs |= {route_source()} - {None}
    return sorted(a for a in addrs if ipaddress.ip_address(a).is_global)
