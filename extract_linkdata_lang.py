from pathlib import Path
import argparse
import struct
import zlib


ALIGN = 0x100
PAYLOAD_START = 0x5100


def align_up(value, alignment=ALIGN):
    return (value + alignment - 1) & ~(alignment - 1)


def unpack(path, outdir):
    data = Path(path).read_bytes()
    total_payload, entry_count, unknown, zero = struct.unpack_from("<4I", data, 0)
    if entry_count != 0x500:
        raise ValueError(f"unexpected entry count: {entry_count:#x}")
    if unknown != 0x100 or zero != 0:
        raise ValueError(f"unexpected header fields: {unknown:#x}, {zero:#x}")

    entries = [
        struct.unpack_from("<4I", data, 0x10 + i * 0x10)
        for i in range(entry_count)
    ]

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    payload_end = PAYLOAD_START
    extracted = []
    for index, (block_offset, flags, packed_size, unpacked_size) in enumerate(entries):
        start = block_offset * ALIGN
        end = start + packed_size
        if flags != 0:
            raise ValueError(f"entry {index} has unexpected flags: {flags:#x}")
        if start < PAYLOAD_START or end > len(data):
            raise ValueError(
                f"entry {index} points outside payload: start={start:#x}, end={end:#x}"
            )

        expected_size, zsize = struct.unpack_from("<2I", data, start)
        cursor = start + 8
        chunks = []

        while sum(len(chunk) for chunk in chunks) < expected_size:
            if chunks:
                zsize = struct.unpack_from("<I", data, cursor)[0]
                cursor += 4
            chunks.append(zlib.decompress(data[cursor : cursor + zsize]))
            cursor += zsize

        terminator = data[cursor:end]
        unpacked = b"".join(chunks)
        if expected_size != unpacked_size or len(unpacked) != unpacked_size:
            raise ValueError(
                f"entry {index} size mismatch: table={unpacked_size}, "
                f"payload={expected_size}, inflated={len(unpacked)}"
            )
        if terminator != b"\0\0\0\0":
            raise ValueError(f"entry {index} has unexpected terminator: {terminator.hex()}")

        name = f"{index:04d}_{block_offset:08x}.bin"
        (outdir / name).write_bytes(unpacked)
        extracted.append((index, block_offset, start, packed_size, unpacked_size, len(chunks)))
        payload_end = max(payload_end, align_up(end))

    if payload_end != len(data):
        raise ValueError(f"payload end mismatch: got {payload_end:#x}, file size {len(data):#x}")

    print(f"header total_payload={total_payload:#x} entries={entry_count} unknown={unknown:#x}")
    print(f"extracted {len(extracted)} entries to {outdir}")
    chunked = [item for item in extracted if item[-1] > 1]
    if chunked:
        print("chunked entries:")
        for index, block_offset, start, packed_size, unpacked_size, chunks in chunked:
            print(
                f"  {index:04d} block={block_offset:#x} offset={start:#x} "
                f"packed={packed_size} unpacked={unpacked_size} chunks={chunks}"
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?", default="LINKDATA_LANG_ENG.BIN")
    parser.add_argument("-o", "--outdir", default="extracted_lang_eng")
    args = parser.parse_args()
    unpack(args.input, args.outdir)


if __name__ == "__main__":
    main()
