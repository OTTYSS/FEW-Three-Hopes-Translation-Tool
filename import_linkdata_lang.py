from pathlib import Path
import argparse
import struct
import zlib


ALIGN = 0x100
PAYLOAD_START = 0x5100
CHUNK_SIZE = 0x8000


def align_up(value, alignment=ALIGN):
    return (value + alignment - 1) & ~(alignment - 1)


def compress_payload(data):
    parts = []
    for pos in range(0, len(data), CHUNK_SIZE):
        chunk = data[pos : pos + CHUNK_SIZE]
        parts.append(zlib.compress(chunk, level=9))

    if not parts:
        parts.append(zlib.compress(b"", level=9))

    payload = bytearray()
    payload.extend(struct.pack("<2I", len(data), len(parts[0])))
    payload.extend(parts[0])
    for part in parts[1:]:
        payload.extend(struct.pack("<I", len(part)))
        payload.extend(part)
    payload.extend(b"\0\0\0\0")
    return bytes(payload)


def import_folder(original_bin, input_folder, output_bin):
    original = Path(original_bin).read_bytes()
    total_payload, entry_count, unknown, zero = struct.unpack_from("<4I", original, 0)
    if entry_count != 0x500:
        raise ValueError(f"unexpected entry count: {entry_count:#x}")
    if unknown != 0x100 or zero != 0:
        raise ValueError(f"unexpected header fields: {unknown:#x}, {zero:#x}")

    input_folder = Path(input_folder)
    old_entries = [
        struct.unpack_from("<4I", original, 0x10 + i * 0x10)
        for i in range(entry_count)
    ]

    payload = bytearray()
    new_entries = []
    for index, (file_id, flags, old_packed_size, old_unpacked_size) in enumerate(old_entries):
        candidates = list(input_folder.glob(f"{index:04d}_{file_id:08x}.bin"))
        if not candidates:
            raise FileNotFoundError(f"missing extracted file for entry {index:04d}, id {file_id:#x}")
        data = candidates[0].read_bytes()
        packed = compress_payload(data)
        offset = PAYLOAD_START + len(payload)
        if offset % ALIGN != 0:
            raise ValueError(f"entry {index:04d} is not aligned: {offset:#x}")
        new_entries.append((offset // ALIGN, flags, len(packed), len(data)))
        payload.extend(packed)
        payload.extend(b"\0" * (align_up(len(packed)) - len(packed)))

    header = bytearray()
    header.extend(struct.pack("<4I", total_payload, entry_count, unknown, zero))
    for entry in new_entries:
        header.extend(struct.pack("<4I", *entry))

    if len(header) > PAYLOAD_START:
        raise ValueError(f"header grew beyond payload start: {len(header):#x}")
    header.extend(b"\0" * (PAYLOAD_START - len(header)))

    output = bytes(header) + bytes(payload)
    Path(output_bin).write_bytes(output)
    print(f"[+] Imported {entry_count} entries from {input_folder}")
    print(f"[+] Wrote {output_bin} ({len(output)} bytes)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("original_bin", nargs="?", default="LINKDATA_LANG_ENG.BIN")
    parser.add_argument("input_folder", nargs="?", default="extracted_lang_eng")
    parser.add_argument("output_bin", nargs="?", default="LINKDATA_LANG_ENG.repacked.BIN")
    args = parser.parse_args()
    import_folder(args.original_bin, args.input_folder, args.output_bin)


if __name__ == "__main__":
    main()
