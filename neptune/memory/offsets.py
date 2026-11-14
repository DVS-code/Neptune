"""Address table and unit conversions."""

from __future__ import annotations

GAME_EXE = "forzahorizon6.exe"
GAME_BUILD = "483.970"

RAD_TO_RPM = 9.549296

ATMOSPHERIC_PSI_DEFAULT = 14.7
ATMOSPHERIC_PSI_MIN = 0.0
ATMOSPHERIC_PSI_MAX = 16.0

PSI_TO_BAR = 0.0689476
NM_TO_LBFT = 0.737562
HP_TO_KW = 0.7457
MS_TO_KPH = 3.6
MS_TO_MPH = 2.2369363
METRE_TO_INCH = 39.3701

_atmospheric_psi = ATMOSPHERIC_PSI_DEFAULT


def atmospheric_psi() -> float:
    return _atmospheric_psi


def set_atmospheric_psi(value: float) -> float:
    global _atmospheric_psi
    value = float(value)
    if value != value:
        return _atmospheric_psi
    _atmospheric_psi = max(ATMOSPHERIC_PSI_MIN, min(ATMOSPHERIC_PSI_MAX, value))
    return _atmospheric_psi


def boost_to_gauge(raw: float) -> float:
    """Convert absolute manifold pressure to the gauge reading."""
    return raw - _atmospheric_psi


class Chain:
    CTX = 0xA8CFEB8
    CTX_SIG = "06 E8 ? ? ? ? 90 48 8D 4D C0 E8 ? ? ? ? 48 8B 15 ? ? ? ? 48 8B 82 A8 07 00 00"
    CTX_SIG_INSN = 16
    CTX_SIG_DISP = CTX_SIG_INSN + 3
    CTX_SIG_INSN_LEN = 7
    CTX_TO_A = 0x1D0
    A_TO_MGR = 0x128
    MGR_TO_LIST = 0x370
    LIST_VEC_BEGIN = 0x18
    LIST_VEC_END = 0x20
    ENTITY_TO_CAR = 0x7740


class Car:
    ENGINE_MODEL = 0x1B0
    MAX_SPEED = 0x24C
    IDLE_SPEED = 0x640
    LIVE_TURBINE = 0x1D0
    LIVE_BOOST = 0x1DC
    LIVE_BOOST_SC = 0x1E4
    GEAR = 0x0B88
    SPEED = 0x14EC

    THROTTLE = 0x1490
    BRAKE = 0x1494
    STEER = 0x1498
    HANDBRAKE = 0x14A0


class EngineModel:
    SPEED = 0x00
    CURVE_BRANCH = 0x84
    ASPIRATION = 0x88
    THRESH = 0x98
    MAX_CLAMP = 0x9C
    INDEX_SCALE = 0xA0
    X_NORM = 0xA4
    CURVE_COUNT = 0xA8
    CURVE = 0xAC
    IDLE = 0x490
    PEAK_TORQUE = 0x494
    NEG_CLAMP = 0x498
    BRAKE_SCALE = 0x484
    BRAKE_KNEE = 0x4A0


class Turbo:
    LOW_AIRFLOW = 0x0AB8
    MAX_SCALE = 0x0ABC
    POWER_MAX = 0x0AC0
    TURBINE_LIMIT = 0x0AC4
    MIN_BOOST = 0x0AC8
    MAX_BOOST = 0x0ACC

    FIELDS = ("low_airflow", "max_scale", "power_max", "turbine_limit", "min_boost", "max_boost")

    BLOCK = LOW_AIRFLOW
    BLOCK_COUNT = len(FIELDS)

    @classmethod
    def offset(cls, name: str) -> int | None:
        return getattr(cls, name.upper(), None) if name in cls.FIELDS else None


class Supercharger:
    CEILING_A = 0x0AF4
    CEILING_B = 0x0AF8
    CEILINGS = (CEILING_A, CEILING_B)


class Wheels:
    BASE = 0x28A8
    STRIDE = 0x0AC0
    COUNT = 4
    ORDER = ("FL", "FR", "RR", "RL")
    RIDE_HEIGHT = 0x02E0
    DROOP_LIMIT = 0x02F0

    RADIUS = 0x0234
    TRAVEL = 0x0018
    LOAD = 0x0050

    FRICTION = 0x0000
    """Live per-wheel slip/friction state the physics recomputes every frame: zero at
    rest, and it swings with cornering load (front and rear swap with turn direction).
    Read-only — it is an output, not a settable base."""

    GRIP_LATERAL = 0x0374
    GRIP_LONGITUDINAL = 0x0378
    """Per-wheel grip SCALE, lateral (cornering) and longitudinal (straight-line).

    Both read ~0.985 on every tyre compound, so they multiply whatever grip the fitted
    tyre already has and leave the compound's own character intact. Plain stored floats:
    one write sticks, and writing the same factor twice does not compound.

    ⚠️ Distinct from the tyre-model mirror at `car+0x1E80..0x1FB0`, which correlated
    beautifully with the telemetry slip channels and whose every field was proven INERT.
    These are per-WHEEL fields in the wheel block, not that car-level block."""

    CAMBER_SIN = 0x0120
    CAMBER_COS = 0x0124

    TOE = 0x0358
    """Static toe, in radians — a plain live scalar (no half-angle sin/cos), writes
    stick immediately and survive a physics tick, matches what the game's own
    tuning menu shows. Sign is per-wheel as stored (not auto-mirrored).
    This field alone is only visually enough for the FRONT wheels."""

    @classmethod
    def addr(cls, car: int, wheel: int, field: int) -> int:
        return car + cls.BASE + wheel * cls.STRIDE + field


class CamberTable:
    """Per-axle suspension-kinematics table baked when a tuning setup is applied.

    `Wheels.CAMBER_SIN`/`CAMBER_COS` is a LIVE value the physics recomputes every
    tick by interpolating through this table against wheel travel — writing it
    directly gets overwritten almost immediately. This table is what the physics
    interpolates FROM, so writing it once (like a tuning-menu change would) sticks.

    Reached as wheel_base + PTR -> axle object + SUB -> the table itself. The front
    axle's two wheels (FL/FR) share one table object; the rear axle's (RR/RL) share
    another. Each table embeds TWO separate curves: the axle's left wheel reads
    entries starting at HEADER (+0x20), the right wheel reads a second, independent
    set of entries starting at SUB_B (+0xD40) — confirmed by decompiling the
    interpolation call site (the mirror argument is hardcoded there, not per-wheel)
    and by writing each region live and checking exactly one wheel moved, for both
    axles. Each of the ENTRY_COUNT entries is a 112-byte kinematic pose;
    CAMBER_SIN/CAMBER_COS within it is the same half-angle (sin, cos) pair as
    Wheels.CAMBER_SIN/CAMBER_COS, in both regions.

    TRACK_X/TRACK_X_B: two points per entry (wheel centre and, going by the offset
    gap, likely the contact patch) that always carry the SAME lateral (X) value —
    confirmed live, at every travel index sampled. That value flips sign between
    a wheel and its mirrored partner while the other axes stay identical (FL
    negative, FR positive), and it changes with travel (scrub), so this is track
    width baked into the same curve camber lives in — a delta from wherever this
    is now, not a target CAMBER_SIN/COS-style absolute (write-tested live: shifted
    a car's front track outward, restored it, no crash, camber untouched).
    """

    PTR = 0x408
    SUB = 0x30
    HEADER = 0x20
    SUB_B = 0xD40
    # Per Wheels.ORDER ('FL','FR','RR','RL'): the axle's left wheel (FL/RL) owns
    # HEADER, the right wheel (FR/RR) owns SUB_B, within the same shared table.
    # The right wheel reads with the opposite sign for the same physical lean
    # (confirmed against the game's own stock values) — callers wanting one
    # symmetric value per axle must mirror it themselves before writing.
    REGION = (HEADER, SUB_B, SUB_B, HEADER)
    ENTRY_SIZE = 112
    ENTRY_COUNT = 30
    CAMBER_SIN = 0x18
    CAMBER_COS = 0x1C
    TRACK_X = 0x00
    TRACK_X_B = 0x50

    TOE_SIN = 0x14


class CarConfig:
    """The car's customization record, reached from the entity.

    Read-only in Neptune: this is where the car's identity is read from. Strings are MSVC
    small-string-optimised — a 16-byte buffer, then the length and the capacity as 8-byte
    fields — so a reader must respect the length rather than scanning for a NUL.

    The record also holds the number plate (`+0x40`), the livery and the installed-parts
    array. Those are documented in docs/CAR_CONFIG_RECORD.md but are not addressed here,
    because nothing in the tool writes this record any more.
    """

    ENTITY_TO_RECORD = 0x2F0

    MEDIA_NAME = 0x00
    LIVERY = 0x20
    CAR_ID = 0x8C

    STRING_CAPACITY = 15
    STRING_LENGTH = 0x10
    STRING_RESERVED = 0x18
    STRING_SIZE = 0x20
    STRING_MAX_LENGTH = 255


class Config:
    ENTITY_TO_CONFIG = 0xDD8
    TORQUE_SCALE = -0x0C
    MAX_RPM = -0x24
    PEAK_TORQUE_RPM = -0x2C
    IDLE_RPM = -0x30
    REDLINE = -0x34
    GEARS = -0x1D0
    REVERSE = -0x1D4
    NUM_GEARS = -0x1D8
    FINAL_DRIVE = -0x1DC


class Lighting:
    """Lighting inputs on the entity. Each frame the game's light evaluator turns these into
    a 15-float register file that every lamp channel's program reads.

    Mapped on 6.483.970 (unchanged since 6.461). Verified live, write then read back:
    MANUAL, SIDELIGHTS, DRL, HIGH_BEAM, FOG and BRAKE_FORCE hold and light their channels.
    HAZARD, LEFT and RIGHT are overwritten every frame, so signals are driven by
    remapping lamp-program operands instead (see `LampRegister`).
    """

    SELECTOR = 0x9231  # 1 = automatic (time of day picks AUTO), 0 = manual (MANUAL)
    AUTO = 0x9232
    BRAKE_FORCE = 0x9238  # forces the brake-light registers on
    KILL = 0x9239  # non-zero zeroes every lamp input
    BRAKE_AMOUNT = 0x923C  # f32, written from the pedals every tick
    MANUAL = 0x9248
    SIDELIGHTS = 0x9249
    DRL = 0x924A
    HIGH_BEAM = 0x924B
    FOG = 0x924C
    REVERSE = 0x924D  # written every tick
    ENGINE = 0x924E  # mirror of ENGINE_ON, written every tick
    HAZARD = 0x924F
    LEFT = 0x9250
    RIGHT = 0x9251
    CONTROLLERS = 0x9258  # sentinel of the lamp-controller list
    CONTROLLER_COUNT = 0x9260
    ENGINE_ON = 0x9278
    CAR_MODE = 0x78E8  # 0 = the local player, 2 = traffic, 3/4 = AI


class LampController:
    """One lamp channel: a slew-smoothed output computed by a short program."""

    OUTPUT = 0x00  # u32 channel id (see LAMP_CHANNELS)
    SLEW = 0x04  # f32 smoothing time constant in seconds; 0 = instant (stock)
    VALUE = 0x10  # f32 current smoothed output, 0..1
    SEQUENCES = 0x18
    INSTRUCTIONS = 0x28  # sentinel of the instruction list
    INSTRUCTION_COUNT = 0x30
    NODE_DATA = 0x10  # list node -> payload
    MAX_COUNT = 128


class LampInstruction:
    """A 24-byte lamp-program instruction: op, dst, a, b, c, imm."""

    SIZE = 24
    OP = 0x00
    DST = 0x04
    A = 0x08
    B = 0x0C
    C = 0x10
    IMM = 0x14
    MAX_COUNT = 64
    # Which register operands each opcode actually reads. Fields an opcode does not use
    # are never rewritten, even if they happen to hold a matching index.
    OPERANDS = {
        1: (A,),
        2: (),
        3: (A,),
        4: (A,),
        5: (A, B),
        6: (A,),
        7: (A, B),
        8: (A, B),
        9: (A,),
        10: (A, B),
        11: (A, B),
        12: (A, B),
        13: (A,),
        14: (A, B),
        15: (A,),
        16: (A, B),
        17: (A,),
        18: (A, B),
        19: (A,),
        20: (A, B, C),
        21: (A, B, C),
    }


class LampRegister:
    """Input registers the light evaluator builds from `Lighting`."""

    ZERO = 0
    BRAKE = 1
    BRAKE_AMOUNT = 2
    SPEED = 4
    LOW_BEAM = 5
    SIDELIGHTS = 6
    DRL = 7
    HIGH_BEAM = 8
    FOG = 9
    REVERSE = 10
    ENGINE = 11  # 1.0 while the engine runs: the "always on" source for remaps
    HAZARD = 12
    LEFT = 13
    RIGHT = 14
    OUTPUT = 27


# The game's own channel names (its `Offset_*` table), shared by every car.
LAMP_CHANNELS = {
    0: "Brake light",
    1: "Reverse light",
    11: "Door open",
    12: "Engine start",
    13: "Headlight",
    14: "Shift light",
    15: "Daytime running light",
    16: "Remote unlock",
    17: "High beam",
    18: "Fog light",
    19: "Sequential brake light",
    20: "Right indicator",
    21: "Left indicator",
    22: "Hazard",
    23: "Open hood",
    24: "Open trunk",
    25: "Rain light",
    26: "Position 1",
    27: "Position 2",
    28: "Position 3",
    29: "Differentiator",
    30: "Electroluminescent livery",
    31: "Convenience light",
    32: "Taillight",
    33: "Brake pulse",
    34: "Sequential brake 1",
    35: "Sequential brake 2",
    36: "Sequential brake 3",
    37: "High beam (separate)",
    38: "DRL (day only)",
    39: "DRL dim",
    40: "Sequential indicator left 1",
    41: "Sequential indicator right 1",
    42: "Sequential indicator left 2",
    43: "Sequential indicator right 2",
    44: "Sequential indicator left 3",
    45: "Sequential indicator right 3",
    46: "Sidelight",
    47: "Number plate light",
    48: "Rear fog light",
    49: "Flashing pattern 1",
    50: "Flashing pattern 2",
    51: "Flashing pattern 3",
    52: "Flashing pattern 4",
    53: "Brake/indicator left",
    54: "Brake/indicator right",
    55: "Brake/reverse",
    56: "Brake/sequential indicator left",
    57: "Brake/sequential indicator right",
    58: "Brake/tail/indicator left",
    59: "Brake/tail/indicator right",
    60: "DRL/brake",
    61: "DRL/brake/indicator left",
    62: "DRL/brake/indicator right",
    63: "DRL/indicator left",
    64: "DRL/indicator right",
    65: "Sidelight/indicator/brake left",
    66: "Sidelight/indicator/brake right",
    67: "Sidelight/indicator left",
    68: "Sidelight/indicator right",
    69: "Tail/brake light",
    70: "Null",
    71: "High headlight",
    72: "Marker light",
}
LAMP_CHANNEL_MAX = 72
NATIVE_STROBE_CHANNEL = 49  # "Flashing pattern 1": a 10 Hz x 1 Hz loop, gated on the engine


class Render:
    """Entity -> render proxy -> model -> scene -> pose. The pose identifies this car's
    model instances among pooled and preview copies."""

    ENTITY_TO_PROXY = 0x7918
    ENTITY_TO_ANIMATION = 0x7920
    PROXY_TO_MODEL = 0x08
    MODEL_TO_SCENE = 0x7B0
    SCENE_TO_POSE = 0x438
    SEARCH_RADIUS = 0x10000000  # instances sit within 256 MiB of the animation component


class ModelInstance:
    POSE = 0x38
    RESOURCE = 0x20  # -> resource; resource + RESOURCE_PATH -> "...\\x.modelbin"
    DEFINITION = 0x28
    RESOURCE_PATH = 0x80
    MATERIALS = 0x140  # on the definition
    MATERIAL_COUNT = 0x148
    ROW_SIZE = 0x38
    ROW_ID = 0x00
    ROW_NAME = 0x08  # std::string
    ROW_MATERIAL = 0x28
    MATERIAL_COMPILED = 0x58
    COMPILED_CPU = 0x10
    COMPILED_CPU_END = 0x18
    COMPILED_BINDING = 0x70
    BINDING_GPU = 0x18  # GPU virtual address of the constant buffer


class LampShader:
    """Constant-buffer layouts of the lamp shaders: (buffer size, RGB tint, channel id).

    The renderer draws from the GPU-mapped copy, so a write there shows on the next frame
    while the CPU copy keeps the authored value to restore from."""

    EMITTER = (0x100, 0x40, 0xC0)  # lbk_* / lights_* lamp emitters
    LENS = (0x80, 0x10, 0x50)  # projector lenses
    LAYOUTS = (EMITTER, LENS)
    MAX_CHANNEL_VALUE = 4.0  # per RGB channel: > 1 also brightens


class GpuUpload:
    """The upload allocator that maps a GPU virtual address to its CPU-visible page."""

    SIGS = (
        "48 8B 05 ? ? ? ? 48 8B 48 20 41 8B 45 3C 48 89 43 10",
        "48 8B 05 ? ? ? ? 48 8B 48 20 41 0F 10 00 0F 29 44 24 40",
        "48 8B 05 ? ? ? ? 48 8B 70 20 49 8B 6F 08 49 8B 3F",
        "48 8B 3D ? ? ? ? 48 85 FF 74 ? 48 8B 4F 20 48 8D 54 24",
    )
    SIG_DISP = 3
    SIG_INSN_LEN = 7
    GLOBAL_FALLBACK = 0xAA6C398  # RVA on 6.483.970
    SERVICE_TO_BACKEND = 0x18
    PAGE_SIZE = 0x50
    PAGES = 0x78
    PAGES_END = 0x80
    GPU_BASE = 0xA0
    PAGE_STRIDE = 16
    PAGE_MAPPED = 0x08
    EXPECTED_PAGE_SIZE = 0x2000000


ASPIRATION_KIND = {
    5: "single",
    6: "twin",
    7: "twin",
    8: "supercharger",
    9: "supercharger",
}


def is_supercharged(sc_boost: float | None, sc_ceiling: float | None = None) -> bool:
    threshold = _atmospheric_psi + 0.5
    if sc_ceiling is not None and sc_ceiling > threshold:
        return True
    return sc_boost is not None and sc_boost > threshold


def is_naturally_aspirated(
    max_boost: float | None,
    turbine_limit: float | None = None,
    sc_boost: float | None = None,
    sc_ceiling: float | None = None,
) -> bool:
    if is_supercharged(sc_boost, sc_ceiling):
        return False
    if max_boost is None:
        return False
    if max_boost > -_atmospheric_psi + 0.5:
        return False
    return turbine_limit is None or abs(turbine_limit) < 1e-6


def aspiration_label(
    aspiration_type: int | None,
    max_boost: float | None = None,
    turbine_limit: float | None = None,
    sc_boost: float | None = None,
    sc_ceiling: float | None = None,
) -> str:
    if is_supercharged(sc_boost, sc_ceiling):
        return "Supercharged"
    if is_naturally_aspirated(max_boost, turbine_limit):
        return "Naturally aspirated"
    kind = ASPIRATION_KIND.get(aspiration_type)
    if kind == "twin":
        return "Twin turbo"
    if kind == "supercharger":
        return "Supercharged"
    if kind == "single":
        return "Turbocharged"
    return "Unknown"
