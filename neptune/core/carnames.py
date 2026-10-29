"""Car display names, read from the installed game's own string table (Data_Car.str).

Falls back to a guess from the internal name (`pretty`) when it can't be read.
"""

from __future__ import annotations

import os
import re
import struct
import zipfile

STRING_TABLE_ZIP = ("media", "Stripped", "StringTables", "EN.zip")
STRING_TABLE_ENTRY = "Data_Car.str"
DISPLAY_NAME_KEY = re.compile(r"IDS_DisplayName_(\d+)")

_game_names: dict[int, str] = {}


def load_game_names(exe_path: str | None) -> int:
    """Read the running game's own car names. Returns how many were found (0 on failure)."""
    global _game_names
    names: dict[int, str] = {}
    if exe_path:
        archive_path = os.path.join(os.path.dirname(exe_path), *STRING_TABLE_ZIP)
        try:
            with zipfile.ZipFile(archive_path) as archive:
                # Entry names in the game's archives are inconsistently cased.
                entry = next(n for n in archive.namelist() if n.lower() == STRING_TABLE_ENTRY.lower())
                strings = parse_string_table(archive.read(entry))
        except (OSError, StopIteration, zipfile.BadZipFile):
            strings = {}
        for key, text in strings.items():
            match = DISPLAY_NAME_KEY.fullmatch(key)
            if match and text.strip():
                names[int(match.group(1))] = text.strip()
    _game_names = names
    return len(names)


def parse_string_table(data: bytes) -> dict[str, str]:
    """{key name: text} from a game .str file, {} if the layout isn't the expected one.

    Two sections of (hash, offset) pairs + NUL-terminated UTF-8: texts, then key names.
    """
    try:
        count = struct.unpack_from("<I", data, 0x94)[0]
        text_blob_size = struct.unpack_from("<I", data, 0x90)[0]
        texts = _read_section(data, 0x98, count)
        names_header = 0x98 + count * 8 + text_blob_size
        _size, names_blob_size, names_count = struct.unpack_from("<III", data, names_header)
        names = _read_section(data, names_header + 12, names_count)
        if names_header + 12 + names_count * 8 + names_blob_size != len(data):
            return {}
        return {names[key]: texts[key] for key in names if key in texts}
    except (struct.error, ValueError, IndexError):
        return {}


def _read_section(data: bytes, table: int, count: int) -> dict[int, str]:
    blob = table + count * 8
    entries = {}
    for index in range(count):
        key, offset = struct.unpack_from("<II", data, table + index * 8)
        end = data.index(b"\0", blob + offset)
        entries[key] = data[blob + offset : end].decode("utf-8")
    return entries


MAKES = {
    "343": "343 Industries",
    "AZM": "Autozam",
    "CHR": "Chrysler",
    "FUN": "Funco",
    "GMA": "Gordon Murray",
    "JIM": "Jimco",
    "KTM": "KTM",
    "LIN": "Lincoln",
    "LR": "Land Rover",
    "LUC": "Lucid",
    "MEY": "Meyers Manx",
    "PEE": "Peel",
    "PEN": "Penhall",
    "RJ": "RJ Anderson",
    "SIE": "Sierra",
    "VIP": "Dodge",
    "WUL": "Wuling",
    "AC": "AC",
    "ACU": "Acura",
    "AH": "Austin-Healey",
    "ALF": "Alfa Romeo",
    "ALP": "Alpine",
    "AMC": "AMC",
    "APO": "Apollo",
    "ARI": "Ariel",
    "AST": "Aston Martin",
    "ATS": "ATS",
    "AUD": "Audi",
    "BAC": "BAC",
    "BEN": "Bentley",
    "BMW": "BMW",
    "BOL": "Bollinger",
    "BRA": "Brabham",
    "BUG": "Bugatti",
    "BUI": "Buick",
    "CAD": "Cadillac",
    "CAN": "Can-Am",
    "CAT": "Caterham",
    "CHE": "Chevrolet",
    "CHY": "Chrysler",
    "CIT": "Citroen",
    "CUP": "Cupra",
    "DAT": "Datsun",
    "DEC": "DeCarbon",
    "DEL": "DeLorean",
    "DET": "Detomaso",
    "DOD": "Dodge",
    "DON": "Donkervoort",
    "DS": "DS",
    "ECT": "Ecto",
    "EMR": "Emory",
    "EUN": "Eunos",
    "EXO": "Exomotive",
    "EXT": "Exeed",
    "FER": "Ferrari",
    "FIA": "Fiat",
    "FOR": "Ford",
    "FRD": "Ford",
    "FMC": "Ford",
    "GIN": "Ginetta",
    "GMC": "GMC",
    "GRE": "Greenwood",
    "HEN": "Hennessey",
    "HOL": "Holden",
    "HON": "Honda",
    "HOO": "Hoonigan",
    "HUM": "Hummer",
    "HUD": "Hudson",
    "HYU": "Hyundai",
    "INF": "Infiniti",
    "INI": "Initial D",
    "ITA": "Italdesign",
    "JAG": "Jaguar",
    "JEE": "Jeep",
    "KOE": "Koenigsegg",
    "LAM": "Lamborghini",
    "LAN": "Lancia",
    "LCN": "Lincoln",
    "LEX": "Lexus",
    "LOC": "Local Motors",
    "LOT": "Lotus",
    "LRO": "Land Rover",
    "LYN": "Lynk & Co",
    "MAS": "Maserati",
    "MAZ": "Mazda",
    "MB": "Mercedes-Benz",
    "MER": "Mercedes-Benz",
    "MCL": "McLaren",
    "MEB": "Mercedes-Benz",
    "MG": "MG",
    "MIN": "MINI",
    "MIT": "Mitsubishi",
    "MOR": "Morgan",
    "MOS": "Mosler",
    "NIS": "Nissan",
    "NOB": "Noble",
    "OPE": "Opel",
    "PAG": "Pagani",
    "PEU": "Peugeot",
    "PGS": "Porsche",
    "PLY": "Plymouth",
    "POL": "Polestar",
    "PON": "Pontiac",
    "POR": "Porsche",
    "RAD": "Radical",
    "RAM": "RAM",
    "REL": "Reliant",
    "REN": "Renault",
    "RIM": "Rimac",
    "RIV": "Rivian",
    "ROL": "Rolls-Royce",
    "ROV": "Rover",
    "SAA": "Saab",
    "SAL": "Saleen",
    "SCI": "Scion",
    "SEA": "SEAT",
    "SHE": "Shelby",
    "SKO": "Skoda",
    "SPA": "Spania",
    "SUB": "Subaru",
    "SUZ": "Suzuki",
    "TES": "Tesla",
    "TOY": "Toyota",
    "TVR": "TVR",
    "ULT": "Ultima",
    "VAU": "Vauxhall",
    "VLK": "Volkswagen",
    "VOL": "Volvo",
    "VW": "Volkswagen",
    "WMO": "W Motors",
    "ZEN": "Zenvo",
}


KEEP = {
    "gt": "GT",
    "gtr": "GT-R",
    "gts": "GTS",
    "gte": "GTE",
    "gtam": "GTAm",
    "gta": "GTA",
    "rs": "RS",
    "sv": "SV",
    "svj": "SVJ",
    "srt": "SRT",
    "ss": "SS",
    "gs": "GS",
    "zl1": "ZL1",
    "z06": "Z06",
    "zr1": "ZR1",
    "amg": "AMG",
    "amr": "AMR",
    "lms": "LMS",
    "lm": "LM",
    "tt": "TT",
    "sq": "SQ",
    "wrx": "WRX",
    "sti": "STI",
    "evo": "Evo",
    "nsx": "NSX",
    "rsx": "RSX",
    "tsx": "TSX",
    "mr2": "MR2",
    "ae86": "AE86",
    "fd": "FD",
    "fc": "FC",
    "r34": "R34",
    "r33": "R33",
    "r32": "R32",
    "gnx": "GNX",
    "db11": "DB11",
    "db5": "DB5",
    "db7": "DB7",
    "dbs": "DBS",
    "dbx": "DBX",
    "m3": "M3",
    "m4": "M4",
    "m5": "M5",
    "m2": "M2",
    "m6": "M6",
    "m8": "M8",
    "x5": "X5",
    "x6": "X6",
    "i8": "i8",
    "e30": "E30",
    "e36": "E36",
    "r8": "R8",
    "tvr": "TVR",
    "v8": "V8",
    "v10": "V10",
    "v12": "V12",
    "f40": "F40",
    "f50": "F50",
    "fxx": "FXX",
    "sf90": "SF90",
    "lfa": "LFA",
    "rcf": "RC F",
    "isf": "IS F",
    "gtc": "GTC",
    "type": "Type",
    "typer": "Type R",
    "types": "Type S",
    "typs": "Type S",
    "ii": "II",
    "iii": "III",
    "iv": "IV",
    "xkr": "XKR",
    "xj": "XJ",
    "f150": "F-150",
    "f450": "F-450",
    "rs3": "RS3",
    "rs4": "RS4",
    "rs5": "RS5",
    "rs6": "RS6",
    "rs7": "RS7",
    "s4": "S4",
    "s5": "S5",
}


GENERIC = {"PG": "Traffic", "NUL": "Unknown"}


EXACT = {
    "wrxsti": "WRX STI",
    "typer": "Type R",
    "types": "Type S",
    "f150": "F-150",
    "f450": "F-450",
    "f450drw": "F-450 DRW",
    "190e": "190E",
    "190efe": "190E FE",
    "300slr": "300 SLR",
    "xbowgt4": "X-Bow GT4",
    "az1": "AZ-1",
    "t50": "T.50",
}


_TRIM = re.compile(r"^(\d{2,4})([A-Z]{1,3})$")
_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _model_words(model: str) -> str:
    """Split a run-together model name into words, keeping known initialisms."""
    exact = EXACT.get(model.lower())
    if exact:
        return exact
    if _TRIM.match(model):
        return model
    parts = [part for part in _SPLIT.split(model) if part]
    words = []
    for part in parts:
        fixed = KEEP.get(part.lower())
        words.append(fixed if fixed else part)
    return " ".join(words)


def pretty(media_name: str | None) -> str:
    """`ALF_Giulia_16` -> `Alfa Romeo Giulia '16`.

    Returns the raw name when it does not follow the convention, so an unrecognised car
    still shows something meaningful rather than a blank.
    """
    if not media_name:
        return ""
    raw = media_name.strip()
    if not raw:
        return ""

    parts = raw.split("_")
    make = MAKES.get(parts[0].upper(), "") if parts else ""
    if not make:
        return raw

    year = ""
    body = parts[1:]
    if body and re.fullmatch(r"\d{2}", body[-1]):
        year = "'" + body[-1]
        body = body[:-1]

    model = " ".join(_model_words(word) if not word.isdigit() else word for word in body)

    return " ".join(piece for piece in (make, model, year) if piece)


def game_name(media_name: str | None, car_id: int | None) -> str:
    """`Lamborghini Gallardo LP570-4 Spyder Performante '12`: model from the game,
    make and year from the internal name (`LAM_..._12`)."""
    model = _game_names.get(car_id) if car_id is not None else None
    if not model:
        return ""
    parts = (media_name or "").strip().split("_")
    make = MAKES.get(parts[0].upper(), "") if parts and parts[0] else ""
    if make and model.lower().startswith(make.lower()):
        make = ""
    year = "'" + parts[-1] if len(parts) > 1 and re.fullmatch(r"\d{2}", parts[-1]) else ""
    return " ".join(piece for piece in (make, model, year) if piece)


def label(media_name: str | None, car_id: int | None = None, fallback: str = "") -> str:
    """A display name: the game's own name when it could be read, else the heuristic guess."""
    return game_name(media_name, car_id) or pretty(media_name) or fallback
