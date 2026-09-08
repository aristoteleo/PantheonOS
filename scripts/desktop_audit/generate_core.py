"""Generate small, disposable image/text/PDF inputs using only the stdlib."""
import argparse
from pathlib import Path
import struct
import zlib


def png() -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\0" + b"".join(
        bytes((240, 180, 30) if 100 < x < 500 and 80 < y < 280 else (35, 95, 190))
        for x in range(640)) for y in range(360))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 640, 360, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def pdf() -> bytes:
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>"]
    for page in (1, 2):
        content = f"BT /F1 24 Tf 50 200 Td (Agent PDF audit page {page}) Tj ET".encode()
        objects.extend([
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << /Font << /F1 7 0 R >> >> /Contents {page * 2 + 2} 0 R >>".encode(),
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        ])
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    data = b"%PDF-1.4\n"
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    data += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    root = parser.parse_args().root.resolve()
    work = root / "workspace"
    work.mkdir(parents=True, exist_ok=True)
    (work / "audit.txt").write_text("Desktop control audit\nOriginal text\n")
    (work / "audit-untouched.txt").write_text("DO NOT CHANGE THIS EDITOR\n")
    (work / "audit.png").write_bytes(png())
    (work / "audit.pdf").write_bytes(pdf())
    print(f"Core fixtures: {work}")
