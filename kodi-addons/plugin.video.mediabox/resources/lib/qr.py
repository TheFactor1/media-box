"""QR code to PNG with no network and no imaging library: qrcodegen (MIT, bundled) + a tiny PNG writer."""
import struct
import zlib

from .qrcodegen import QrCode


def png(width, height, rows):
    """8-bit greyscale PNG; rows are bytes of length width."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    raw = b''.join(b'\x00' + r for r in rows)  # filter type 0 per row
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def qr_png(text, scale=12, border=4):
    code = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    n = code.get_size() + 2 * border
    rows = []
    for y in range(n):
        row = bytes(0 if code.get_module(x - border, y - border) else 255 for x in range(n) for _ in range(scale))
        rows += [row] * scale
    return png(n * scale, n * scale, rows)


def solid_png(grey=0):
    return png(1, 1, [bytes([grey])])


if __name__ == '__main__':  # self-check: python -m resources.lib.qr (from the add-on folder)
    data = qr_png('http://192.168.1.2:8097/setup?n=abc')
    assert data[:8] == b'\x89PNG\r\n\x1a\n' and zlib.decompress(data[data.index(b'IDAT') + 4:-16])
    print('ok', len(data), 'bytes')
