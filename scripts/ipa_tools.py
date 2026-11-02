"""Reads what a SideStore source needs from an .ipa: bundle ID, name, versions,
privacy permissions, entitlements and the app icon. Standard library only.

Run directly to print "bundle-id<TAB>version<TAB>build<TAB>name" for an IPA.
"""
import plistlib
import re
import struct
import sys
import zipfile
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# SideStore/AltStore ignore these when comparing an app's entitlements with its source.
IGNORED_ENTITLEMENTS = {
    "application-identifier",
    "com.apple.developer.team-identifier",
    "get-task-allow",
    "keychain-access-groups",
}


class NotAnApp(Exception):
    pass


def inspect_ipa(path):
    """Returns (metadata dict, icon PNG bytes or None)."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        info_name = next((n for n in names if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)), None)
        if info_name is None:
            raise NotAnApp("no Payload/<name>.app/Info.plist inside")
        app_dir = info_name[: -len("Info.plist")]
        info = plistlib.loads(z.read(info_name))
        if "CFBundleIdentifier" not in info:
            raise NotAnApp("Info.plist has no CFBundleIdentifier")

        entitlements = set()
        executable = info.get("CFBundleExecutable")
        if executable and app_dir + executable in names:
            entitlements = read_entitlements(z.read(app_dir + executable))

        icon = find_icon(z, names, app_dir, info)

    version = info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or "1.0"
    build = info.get("CFBundleVersion") or version
    meta = {
        "bundleIdentifier": str(info["CFBundleIdentifier"]),
        "name": str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or info["CFBundleIdentifier"]),
        "version": str(version),
        "buildVersion": str(build),
        "minOSVersion": str(info.get("MinimumOSVersion", "")),
        "privacy": {
            key: str(value)
            for key, value in sorted(info.items())
            if key.startswith("NS") and key.endswith("UsageDescription")
        },
        "entitlements": sorted(entitlements - IGNORED_ENTITLEMENTS),
    }
    return meta, icon


# MARK: - Entitlements (from the code signature in the Mach-O executable)

def read_entitlements(binary):
    keys = set()
    try:
        for offset in macho_slices(binary):
            keys |= slice_entitlements(binary, offset)
    except (struct.error, IndexError, ValueError):
        pass
    return keys


def macho_slices(data):
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic in (0xCAFEBABE, 0xCAFEBABF):  # universal binary
        count = struct.unpack_from(">I", data, 4)[0]
        offsets = []
        for i in range(min(count, 16)):
            if magic == 0xCAFEBABE:
                offsets.append(struct.unpack_from(">IIIII", data, 8 + i * 20)[2])
            else:
                offsets.append(struct.unpack_from(">IIQQII", data, 8 + i * 32)[2])
        return offsets
    return [0]


def slice_entitlements(data, base):
    magic = struct.unpack_from("<I", data, base)[0]
    if magic == 0xFEEDFACF:
        header_size = 32
    elif magic == 0xFEEDFACE:
        header_size = 28
    else:
        return set()
    command_count = struct.unpack_from("<I", data, base + 16)[0]
    position = base + header_size
    for _ in range(command_count):
        command, size = struct.unpack_from("<II", data, position)
        if command == 0x1D:  # LC_CODE_SIGNATURE
            data_offset = struct.unpack_from("<I", data, position + 8)[0]
            return blob_entitlements(data, base + data_offset)
        if size == 0:
            break
        position += size
    return set()


def blob_entitlements(data, start):
    magic, _, count = struct.unpack_from(">III", data, start)
    if magic != 0xFADE0CC0:  # embedded signature super-blob
        return set()
    for i in range(min(count, 64)):
        slot, offset = struct.unpack_from(">II", data, start + 12 + i * 8)
        if slot == 5:  # entitlements, as an XML plist
            blob_magic, length = struct.unpack_from(">II", data, start + offset)
            if blob_magic == 0xFADE7171:
                xml = data[start + offset + 8 : start + offset + length]
                try:
                    return set(plistlib.loads(xml).keys())
                except Exception:
                    return set()
    return set()


# MARK: - Icon

def find_icon(z, names, app_dir, info):
    bases = []
    for key in ("CFBundleIcons", "CFBundleIcons~ipad"):
        icons = info.get(key)
        if isinstance(icons, dict):
            primary = icons.get("CFBundlePrimaryIcon")
            if isinstance(primary, dict):
                bases += [str(b) for b in primary.get("CFBundleIconFiles", []) or []]
    bases += [str(b) for b in info.get("CFBundleIconFiles", []) or []]

    top_level_pngs = [
        n for n in names
        if n.startswith(app_dir) and "/" not in n[len(app_dir):] and n.lower().endswith(".png")
    ]
    candidates = [n for n in top_level_pngs if any(n[len(app_dir):].startswith(b) for b in bases)]
    if not candidates:
        candidates = [n for n in top_level_pngs if re.match(r"(?i)(appicon|icon)", n[len(app_dir):])]

    best, best_area = None, 0
    for name in candidates:
        png = normalize_png(z.read(name))
        if png is None:
            continue
        width, height = struct.unpack(">II", png[16:24])
        if width * height > best_area:
            best, best_area = png, width * height
    return best


def normalize_png(data):
    """Returns a standard PNG. Xcode stores iOS PNGs in Apple's "CgBI" variant
    (raw deflate, blue and red swapped), which browsers and SideStore can't show."""
    if not data.startswith(PNG_SIGNATURE):
        return None
    chunks = []
    position = 8
    while position + 8 <= len(data):
        length = struct.unpack(">I", data[position:position + 4])[0]
        kind = data[position + 4:position + 8]
        chunks.append((kind, data[position + 8:position + 8 + length]))
        position += 12 + length
        if kind == b"IEND":
            break
    kinds = [kind for kind, _ in chunks]
    if b"IHDR" not in kinds:
        return None
    if b"CgBI" not in kinds:
        return data

    header = next(body for kind, body in chunks if kind == b"IHDR")
    width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", header)
    if depth != 8 or color != 6 or interlace != 0:
        return None
    try:
        raw = zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"), -15)
    except zlib.error:
        return None
    stride = width * 4 + 1
    if len(raw) < stride * height:
        return None
    pixels = bytearray(raw[: stride * height])
    # Swapping blue and red is safe on filtered rows: PNG filters work per channel.
    for row in range(height):
        start = row * stride + 1
        line = pixels[start:start + width * 4]
        line[0::4], line[2::4] = line[2::4], line[0::4]
        pixels[start:start + width * 4] = line

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    return PNG_SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(pixels), 9)) + chunk(b"IEND", b"")


if __name__ == "__main__":
    meta, _ = inspect_ipa(sys.argv[1])
    print("\t".join([meta["bundleIdentifier"], meta["version"], meta["buildVersion"], meta["name"]]))
