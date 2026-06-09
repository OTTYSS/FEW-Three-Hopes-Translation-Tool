## Requirements

- Python 3.10 or newer recommended.

---

## Files

| File | Purpose |
| --- | --- |
| `localiz_gui.py` | Main graphical application. |
| `extract_linkdata_lang.py` | Command-line extractor for LINKDATA language archives. |
| `lang_folder_csv.py` | Command-line CSV export/import tool for extracted `.bin` files. |
| `import_linkdata_lang.py` | Command-line repacker for rebuilding LINKDATA archives. |

---

## How to Use

### 1. Extract LINKDATA

Open the `1. Extract LINKDATA` tab.

- `Input BIN`: select your original language archive, for example `LINKDATA_LANG_ENG.BIN`.
- `Output folder`: choose where extracted files should be written, for example `extracted_lang_eng`.
- Click `Extract`.

The output folder will contain files named like:

```text
0000_12345678.bin
0001_9abcdef0.bin
...
```

### 2. Export Text to CSV

Open the `2. CSV` tab.

- `Extracted folder`: select the folder created during extraction.
- `CSV file`: choose an output CSV file, for example `lang.csv`.
- Click `Export Folder to CSV`.

The CSV contains these important columns:

| Column | Description |
| --- | --- |
| `original` | Original text extracted from the archive. |
| `translated` | Put your translated text here. |

Only edit the `translated` column.

### 3. Import Translations

After editing the CSV:

- Open the `2. CSV` tab.
- Select the same extracted folder.
- Select your edited CSV file.
- Click `Import CSV to Folder`.

Rows with an empty `translated` value will keep the original text.

### 4. Repack LINKDATA

Open the `3. Repack LINKDATA` tab.

- `Original BIN`: select the original archive.
- `Modified folder`: select the extracted folder after CSV import.
- `Output BIN`: choose the repacked output file, for example `LINKDATA_LANG_ENG.repacked.BIN`.
- Click `Repack`.

---

## Command-Line Usage

The original scripts can still be used without the GUI.

Extract:

```powershell
python .\extract_linkdata_lang.py LINKDATA_LANG_ENG.BIN -o extracted_lang_eng
```

Export CSV:

```powershell
python .\lang_folder_csv.py -export extracted_lang_eng lang.csv
```

Import CSV:

```powershell
python .\lang_folder_csv.py -import lang.csv extracted_lang_eng
```

Repack:

```powershell
python .\import_linkdata_lang.py LINKDATA_LANG_ENG.BIN extracted_lang_eng LINKDATA_LANG_ENG.repacked.BIN
```

---

## Credits

Thanks to **[Ahtheerr](https://github.com/Ahtheerr)** for consultation.