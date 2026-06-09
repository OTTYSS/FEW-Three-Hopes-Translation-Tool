from pathlib import Path
import argparse
import csv
import shutil
import struct


CSV_FIELDS = [
    "file",
    "record",
    "group",
    "meta_a",
    "meta_b",
    "start",
    "end",
    "length",
    "original",
    "translated",
]


def decode_string(data, start, length):
    raw = data[start : start + length]
    if raw.endswith(b"\0"):
        raw = raw[:-1]
    return raw.decode("utf-8", errors="replace")


def parse_inner(path):
    data = Path(path).read_bytes()
    if len(data) < 0x20:
        raise ValueError(f"{path}: too small")

    version, header_size, zero_a, zero_b, count, group = struct.unpack_from("<6I", data, 0)
    if (version, header_size, zero_a, zero_b) != (1, 0x10, 0, 0):
        raise ValueError(f"{path}: unexpected header")
    if count < 1 or 0x18 + count * 8 > len(data):
        raise ValueError(f"{path}: bad record count {count}")

    records = [
        struct.unpack_from("<2I", data, 0x18 + i * 8)
        for i in range(count)
    ]
    meta_a, meta_b = records[0]

    strings = []
    for record_index, (length, end_minus_10) in enumerate(records[1:], start=1):
        if length == 0 or length == 0xFFFFFFFF:
            continue
        end = end_minus_10 + 0x10
        start = end - length
        if start < 0 or end > len(data) or start > end:
            continue
        strings.append(
            {
                "record": record_index,
                "length": length,
                "start": start,
                "end": end,
                "end_minus_10": end_minus_10,
                "text": decode_string(data, start, length),
            }
        )

    return {
        "data": data,
        "count": count,
        "group": group,
        "records": records,
        "meta_a": meta_a,
        "meta_b": meta_b,
        "strings": strings,
    }


def export_folder(input_folder, output_csv):
    input_folder = Path(input_folder)
    rows = []
    for path in sorted(input_folder.glob("*.bin")):
        info = parse_inner(path)
        for item in info["strings"]:
            if item["text"] == "":
                continue
            rows.append(
                {
                    "file": path.name,
                    "record": item["record"],
                    "group": info["group"],
                    "meta_a": f"{info['meta_a']:08x}",
                    "meta_b": f"{info['meta_b']:08x}",
                    "start": item["start"],
                    "end": item["end"],
                    "length": item["length"],
                    "original": item["text"],
                    "translated": "",
                }
            )

    with open(output_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[+] Exported {len(rows)} rows from {input_folder} to {output_csv}")


def load_translations(input_csv):
    by_file = {}
    with open(input_csv, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            file_name = row["file"]
            record = int(row["record"])
            original = row["original"]
            translated = row.get("translated", "")
            text = translated if translated != "" else original
            by_file.setdefault(file_name, {})[record] = text
    return by_file


def rebuild_file(path, translations):
    info = parse_inner(path)
    data = info["data"]
    strings = info["strings"]
    if not strings:
        return False

    strings_by_offset = sorted(strings, key=lambda item: (item["start"], item["end"], item["record"]))
    first_string_start = strings_by_offset[0]["start"]
    last_string_end = max(item["end"] for item in strings_by_offset)
    rebuilt = bytearray(data[:first_string_start])
    records = list(info["records"])
    cursor = first_string_start

    for item in strings_by_offset:
        if item["start"] < cursor:
            raise ValueError(f"{path}: overlapping string records near record {item['record']}")
        rebuilt.extend(data[cursor : item["start"]])
        text = translations.get(item["record"], item["text"])
        raw = text.encode("utf-8") + b"\0"
        start = len(rebuilt)
        rebuilt.extend(raw)
        end = len(rebuilt)
        records[item["record"]] = (len(raw), end - 0x10)
        cursor = item["end"]

    rebuilt.extend(data[last_string_end:])

    output = bytearray(rebuilt)
    for index, (a, b) in enumerate(records):
        off = 0x18 + index * 8
        output[off : off + 8] = struct.pack("<2I", a, b)

    output.extend(b"\0" * ((16 - len(output) % 16) % 16))

    if output == data:
        return False
    path.write_bytes(output)
    return True


def import_csv(input_csv, output_folder):
    output_folder = Path(output_folder)
    translations = load_translations(input_csv)
    changed = 0
    missing = []

    for file_name, file_translations in sorted(translations.items()):
        path = output_folder / file_name
        if not path.exists():
            missing.append(file_name)
            continue
        if rebuild_file(path, file_translations):
            changed += 1

    print(f"[+] Imported {input_csv} into {output_folder}")
    print(f"[+] Changed {changed} files")
    if missing:
        print(f"[!] Missing {len(missing)} files; first missing: {missing[:5]}")


def main():
    parser = argparse.ArgumentParser(
        usage=(
            "python lang_folder_csv.py -export <input_folder> <output.csv>\n"
            "python lang_folder_csv.py -import <input.csv> <output_folder>"
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-export", action="store_true")
    mode.add_argument("-import", dest="do_import", action="store_true")
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()

    if args.export:
        export_folder(args.input, args.output)
    else:
        import_csv(args.input, args.output)


if __name__ == "__main__":
    main()
