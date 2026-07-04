from pathlib import Path
import csv
import queue
import struct
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import zlib


ALIGN = 0x100
PAYLOAD_START = 0x5100
CHUNK_SIZE = 0x8000

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


def align_up(value, alignment=ALIGN):
    return (value + alignment - 1) & ~(alignment - 1)


def decode_string(data, start, length):
    raw = data[start : start + length]
    if raw.endswith(b"\0"):
        raw = raw[:-1]
    return raw.decode("utf-8", errors="replace")


def looks_like_text(text):
    if text == "":
        return False
    printable = sum(1 for ch in text if ch in "\r\n\t" or ch.isprintable())
    return printable == len(text)


def unpack_linkdata(path, outdir, log=print):
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

    log(f"header total_payload={total_payload:#x} entries={entry_count} unknown={unknown:#x}")
    log(f"extracted {len(extracted)} entries to {outdir}")
    chunked = [item for item in extracted if item[-1] > 1]
    if chunked:
        log("chunked entries:")
        for index, block_offset, start, packed_size, unpacked_size, chunks in chunked:
            log(
                f"  {index:04d} block={block_offset:#x} offset={start:#x} "
                f"packed={packed_size} unpacked={unpacked_size} chunks={chunks}"
            )


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


def find_extracted_file(input_folder, index, old_block_offset):
    exact = input_folder / f"{index:04d}_{old_block_offset:08x}.bin"
    if exact.exists():
        return exact

    candidates = sorted(input_folder.glob(f"{index:04d}_*.bin"))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(
            f"missing extracted file for entry {index:04d}, old block {old_block_offset:#x}"
        )
    raise FileExistsError(f"multiple extracted files match entry {index:04d}: {candidates[:5]}")


def import_folder_to_linkdata(original_bin, input_folder, output_bin, log=print):
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
    for index, (old_block_offset, flags, _old_packed_size, _old_unpacked_size) in enumerate(old_entries):
        data = find_extracted_file(input_folder, index, old_block_offset).read_bytes()
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
    log(f"[+] Imported {entry_count} entries from {input_folder}")
    log(f"[+] Wrote {output_bin} ({len(output)} bytes)")


def parse_inner(path):
    data = Path(path).read_bytes()
    if len(data) < 0x20:
        raise ValueError(f"{path}: too small")

    version, header_size, zero_a, zero_b, count, group = struct.unpack_from("<6I", data, 0)
    if (version, header_size, zero_a, zero_b) != (1, 0x10, 0, 0):
        raise ValueError(f"{path}: unexpected header")
    table_end = 0x18 + count * 8
    if count < 1 or table_end + 4 > len(data):
        raise ValueError(f"{path}: bad record count {count}")

    records = [
        struct.unpack_from("<2I", data, 0x18 + i * 8)
        for i in range(count)
    ]
    meta_a, meta_b = records[0]
    tail_length = struct.unpack_from("<I", data, table_end)[0]

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
                "indexed": True,
                "tail_length_offset": None,
                "text": decode_string(data, start, length),
            }
        )

    tail_start = max((item["end"] for item in strings), default=table_end + 4)
    if tail_length not in (0, 0xFFFFFFFF):
        tail_end = tail_start + tail_length
        if tail_start <= tail_end <= len(data):
            raw_tail = data[tail_start:tail_end]
            if raw_tail.endswith(b"\0"):
                text = decode_string(data, tail_start, tail_length)
                if text == "" or looks_like_text(text):
                    strings.append(
                        {
                            "record": count,
                            "length": tail_length,
                            "start": tail_start,
                            "end": tail_end,
                            "end_minus_10": tail_end - 0x10,
                            "indexed": False,
                            "tail_length_offset": table_end,
                            "text": text,
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


def export_folder_to_csv(input_folder, output_csv, log=print):
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

    log(f"[+] Exported {len(rows)} rows from {input_folder} to {output_csv}")


def load_translations(input_csv):
    by_file = {}
    with open(input_csv, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            translated = row.get("translated", "")
            if translated == "":
                continue
            file_name = row["file"]
            record = int(row["record"])
            by_file.setdefault(file_name, {})[record] = translated
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
        if item.get("indexed", True):
            records[item["record"]] = (len(raw), end - 0x10)
        else:
            tail_length_offset = item.get("tail_length_offset")
            if tail_length_offset is not None:
                rebuilt[tail_length_offset : tail_length_offset + 4] = struct.pack("<I", len(raw))
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


def import_csv_to_folder(input_csv, output_folder, log=print):
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

    log(f"[+] Imported {input_csv} into {output_folder}")
    log(f"[+] Changed {changed} files")
    if missing:
        log(f"[!] Missing {len(missing)} files; first missing: {missing[:5]}")


class PathRow(ttk.Frame):
    def __init__(self, master, label, mode, default=""):
        super().__init__(master, style="Panel.TFrame")
        self.mode = mode
        self.value = tk.StringVar(value=default)
        ttk.Label(self, text=label, width=19, style="FieldLabel.TLabel").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Entry(self, textvariable=self.value, style="Path.TEntry").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(self, text="Browse", command=self.browse, style="Secondary.TButton").pack(side=tk.LEFT, padx=(10, 0))

    def browse(self):
        if self.mode == "open_file":
            path = filedialog.askopenfilename()
        elif self.mode == "save_file":
            path = filedialog.asksaveasfilename()
        elif self.mode == "folder":
            path = filedialog.askdirectory()
        else:
            path = ""
        if path:
            self.value.set(path)

    def get(self):
        return self.value.get().strip()


class LocalizApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FEW: Three Hopes Translation Tool")
        self.geometry("980x700")
        self.minsize(880, 620)
        self.log_queue = queue.Queue()
        self.worker = None
        self.status = tk.StringVar(value="Ready")

        self._setup_style()
        self._build_ui()
        self.after(100, self._drain_log_queue)

    def _setup_style(self):
        self.colors = {
            "bg": "#f5f7fb",
            "panel": "#f5f7fb",
            "panel_alt": "#f5f7fb",
            "border": "#cdd6e3",
            "text": "#17212f",
            "muted": "#657386",
            "accent": "#2563eb",
            "accent_hover": "#1d4ed8",
            "success": "#0f8a5f",
            "danger": "#b42318",
            "log_bg": "#111827",
            "log_fg": "#d1d5db",
        }
        self.configure(bg=self.colors["bg"])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", font=("Segoe UI", 10), background=self.colors["bg"], foreground=self.colors["text"])
        style.configure("Root.TFrame", background=self.colors["bg"])
        style.configure("Header.TFrame", background=self.colors["bg"])
        style.configure("HeaderTitle.TLabel", background=self.colors["bg"], foreground=self.colors["text"], font=("Segoe UI Semibold", 18))
        style.configure("HeaderSub.TLabel", background=self.colors["bg"], foreground=self.colors["muted"], font=("Segoe UI", 10))
        style.configure("Panel.TFrame", background=self.colors["panel"])
        style.configure(
            "Card.TLabelframe",
            background=self.colors["panel"],
            bordercolor=self.colors["border"],
            relief=tk.SOLID,
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=self.colors["panel"],
            foreground=self.colors["text"],
            font=("Segoe UI Semibold", 11),
        )
        style.configure("FieldLabel.TLabel", background=self.colors["panel"], foreground=self.colors["muted"])
        style.configure("Status.TLabel", background=self.colors["bg"], foreground=self.colors["muted"])
        style.configure("StatusValue.TLabel", background=self.colors["bg"], foreground=self.colors["text"], font=("Segoe UI Semibold", 10))
        style.configure("TNotebook", background=self.colors["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", padding=(18, 10), font=("Segoe UI Semibold", 10), background=self.colors["bg"])
        style.map(
            "TNotebook.Tab",
            background=[("selected", self.colors["bg"]), ("active", self.colors["bg"])],
            foreground=[("selected", self.colors["accent"])],
        )
        style.configure("Path.TEntry", padding=(8, 6), fieldbackground="#ffffff", bordercolor=self.colors["border"])
        style.configure("Primary.TButton", padding=(18, 9), font=("Segoe UI Semibold", 10), background=self.colors["accent"], foreground="#ffffff")
        style.map("Primary.TButton", background=[("active", self.colors["accent_hover"])], foreground=[("disabled", "#e5e7eb")])
        style.configure("Secondary.TButton", padding=(12, 7), background="#eef2f7", foreground=self.colors["text"])
        style.map("Secondary.TButton", background=[("active", "#e2e8f0")])
        style.configure("Danger.TButton", padding=(12, 7), background="#fff1f0", foreground=self.colors["danger"])
        style.map("Danger.TButton", background=[("active", "#ffe4e1")])

    def _build_ui(self):
        root = ttk.Frame(self, padding=14, style="Root.TFrame")
        root.pack(fill=tk.BOTH, expand=True)

        tab_bar = tk.Frame(root, bg=self.colors["bg"])
        tab_bar.pack(fill=tk.X)

        content = tk.Frame(root, bg=self.colors["bg"])
        content.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self.tabs = {}
        self.tab_buttons = {}
        self.extract_tab = tk.Frame(content, bg=self.colors["bg"], padx=0, pady=0)
        self.csv_tab = tk.Frame(content, bg=self.colors["bg"], padx=0, pady=0)
        self.pack_tab = tk.Frame(content, bg=self.colors["bg"], padx=0, pady=0)
        self.tabs = {
            "extract": self.extract_tab,
            "csv": self.csv_tab,
            "pack": self.pack_tab,
        }
        for key, text in (
            ("extract", "1. Extract LINKDATA"),
            ("csv", "2. CSV"),
            ("pack", "3. Repack LINKDATA"),
        ):
            button = tk.Button(
                tab_bar,
                text=text,
                command=lambda name=key: self.show_tab(name),
                bg=self.colors["bg"],
                fg=self.colors["text"],
                activebackground=self.colors["bg"],
                activeforeground=self.colors["accent"],
                bd=0,
                relief=tk.FLAT,
                padx=14,
                pady=8,
                font=("Segoe UI Semibold", 10),
                cursor="hand2",
            )
            button.pack(side=tk.LEFT, padx=(0, 6))
            self.tab_buttons[key] = button

        extract_card = self._card(self.extract_tab, "Extract archive")
        self.extract_input = PathRow(extract_card, "Input BIN", "open_file", "LINKDATA_LANG_ENG.BIN")
        self.extract_output = PathRow(extract_card, "Output folder", "folder", "extracted_lang_eng")
        self.extract_input.pack(fill=tk.X, pady=(0, 10))
        self.extract_output.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(
            extract_card,
            text="Extract",
            style="Primary.TButton",
            command=lambda: self.run_task(
                "Extracting LINKDATA",
                unpack_linkdata,
                self.extract_input.get(),
                self.extract_output.get(),
            ),
        ).pack(anchor=tk.E, pady=(8, 0))

        csv_card = self._card(self.csv_tab, "CSV translation workflow")
        self.csv_folder = PathRow(csv_card, "Extracted folder", "folder", "extracted_lang_eng")
        self.csv_file = PathRow(csv_card, "CSV file", "save_file", "lang.csv")
        self.csv_folder.pack(fill=tk.X, pady=(0, 10))
        self.csv_file.pack(fill=tk.X, pady=(0, 10))
        csv_buttons = ttk.Frame(csv_card, style="Panel.TFrame")
        csv_buttons.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(
            csv_buttons,
            text="Export Folder to CSV",
            style="Primary.TButton",
            command=lambda: self.run_task(
                "Exporting CSV",
                export_folder_to_csv,
                self.csv_folder.get(),
                self.csv_file.get(),
            ),
        ).pack(side=tk.RIGHT)
        ttk.Button(
            csv_buttons,
            text="Import CSV to Folder",
            style="Secondary.TButton",
            command=lambda: self.run_task(
                "Importing CSV",
                import_csv_to_folder,
                self.csv_file.get(),
                self.csv_folder.get(),
            ),
        ).pack(side=tk.RIGHT, padx=(0, 8))

        pack_card = self._card(self.pack_tab, "Build final archive")
        self.pack_original = PathRow(pack_card, "Original BIN", "open_file", "LINKDATA_LANG_ENG.BIN")
        self.pack_folder = PathRow(pack_card, "Modified folder", "folder", "extracted_lang_eng")
        self.pack_output = PathRow(pack_card, "Output BIN", "save_file", "LINKDATA_LANG_ENG.repacked.BIN")
        self.pack_original.pack(fill=tk.X, pady=(0, 10))
        self.pack_folder.pack(fill=tk.X, pady=(0, 10))
        self.pack_output.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(
            pack_card,
            text="Repack",
            style="Primary.TButton",
            command=lambda: self.run_task(
                "Repacking LINKDATA",
                import_folder_to_linkdata,
                self.pack_original.get(),
                self.pack_folder.get(),
                self.pack_output.get(),
            ),
        ).pack(anchor=tk.E, pady=(8, 0))

        log_frame = tk.Frame(root, bg=self.colors["bg"])
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(14, 0))
        tk.Label(
            log_frame,
            text="Log",
            bg=self.colors["bg"],
            fg=self.colors["text"],
            font=("Segoe UI Semibold", 10),
        ).pack(anchor=tk.W, pady=(0, 6))
        log_box = tk.Frame(log_frame, bg=self.colors["log_bg"], padx=10, pady=10)
        log_box.pack(fill=tk.BOTH, expand=True)
        self.log = tk.Text(
            log_box,
            height=12,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg=self.colors["log_bg"],
            fg=self.colors["log_fg"],
            insertbackground=self.colors["log_fg"],
            relief=tk.FLAT,
            padx=12,
            pady=10,
            font=("Consolas", 10),
        )
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(log_box, command=self.log.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log.configure(yscrollcommand=scroll.set)

        status_bar = ttk.Frame(root, style="Root.TFrame")
        status_bar.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(status_bar, text="Status:", style="Status.TLabel").pack(side=tk.LEFT)
        ttk.Label(status_bar, textvariable=self.status, style="StatusValue.TLabel").pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(status_bar, text="Clear Log", command=self.clear_log, style="Danger.TButton").pack(side=tk.RIGHT)
        self.show_tab("extract")

    def _card(self, parent, title):
        wrapper = tk.Frame(parent, bg=self.colors["bg"])
        wrapper.pack(fill=tk.X, anchor=tk.N, pady=(0, 12))
        tk.Label(
            wrapper,
            text=title,
            bg=self.colors["bg"],
            fg=self.colors["text"],
            font=("Segoe UI Semibold", 11),
        ).pack(anchor=tk.W, pady=(0, 8))
        card = ttk.Frame(wrapper, padding=16, style="Panel.TFrame")
        card.pack(fill=tk.X)
        return card

    def show_tab(self, active):
        for key, frame in self.tabs.items():
            if key == active:
                frame.pack(fill=tk.BOTH, expand=True)
            else:
                frame.pack_forget()
        for key, button in self.tab_buttons.items():
            selected = key == active
            button.configure(
                fg=self.colors["accent"] if selected else self.colors["text"],
                font=("Segoe UI Semibold", 10) if selected else ("Segoe UI", 10),
            )

    def _pack_rows(self, parent, *rows):
        for row in rows:
            row.pack(fill=tk.X, pady=(0, 8))

    def run_task(self, title, func, *args):
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("Busy", "Another operation is still running.")
            return
        if any(not arg for arg in args):
            messagebox.showwarning("Missing path", "Please fill in all paths for this operation.")
            return

        self.status.set(title)
        self.append_log(f"\n== {title} ==")

        def log(message):
            self.log_queue.put(str(message))

        def target():
            try:
                func(*args, log=log)
                self.log_queue.put("[OK] Done")
                self.log_queue.put(("STATUS", "Ready"))
            except Exception as exc:
                self.log_queue.put(f"[ERROR] {exc}")
                self.log_queue.put(("STATUS", "Error"))

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _drain_log_queue(self):
        while True:
            try:
                item = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, tuple) and item[0] == "STATUS":
                self.status.set(item[1])
            else:
                self.append_log(item)
        self.after(100, self._drain_log_queue)

    def append_log(self, message):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, f"{message}\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def clear_log(self):
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)


def main():
    app = LocalizApp()
    app.mainloop()


if __name__ == "__main__":
    main()
