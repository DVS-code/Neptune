"""Address table and unit conversions."""
from __future__ import annotations

GAME_EXE = 'forzahorizon6.exe'
GAME_BUILD = '420.696'

RAD_TO_RPM = 9.549296

ATMOSPHERIC_PSI_DEFAULT = 14.7
ATMOSPHERIC_PSI_MIN = 0.0
ATMOSPHERIC_PSI_MAX = 16.0

PSI_TO_BAR = 0.0689476
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
    CTX = 0xA894868
    CTX_SIG = ('06 E8 ? ? ? ? 90 48 8D 4D C0 E8 ? ? ? ? '
               '48 8B 15 ? ? ? ? 48 8B 82 A8 07 00 00')
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

    FIELDS = ('low_airflow', 'max_scale', 'power_max',
              'turbine_limit', 'min_boost', 'max_boost')

    # The six fields are contiguous, in FIELDS order, from LOW_AIRFLOW. That lets the
    # whole block be read in one call instead of six — see `Vehicle.turbo_block`.
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
    ORDER = ('FL', 'FR', 'RR', 'RL')
    RIDE_HEIGHT = 0x02E0
    DROOP_LIMIT = 0x02F0


    RADIUS = 0x0234
    TRAVEL = 0x0018
    LOAD = 0x0050

    @classmethod
    def addr(cls, car: int, wheel: int, field: int) -> int:
        return car + cls.BASE + wheel * cls.STRIDE + field


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


ASPIRATION_KIND = {
    5: 'single',
    6: 'twin',
    7: 'twin',
    8: 'supercharger',
    9: 'supercharger',
}


def is_supercharged(sc_boost: float | None, sc_ceiling: float | None = None) -> bool:
    threshold = _atmospheric_psi + 0.5
    if sc_ceiling is not None and sc_ceiling > threshold:
        return True
    return sc_boost is not None and sc_boost > threshold


def is_naturally_aspirated(max_boost: float | None, turbine_limit: float | None = None,
                           sc_boost: float | None = None,
                           sc_ceiling: float | None = None) -> bool:
    if is_supercharged(sc_boost, sc_ceiling):
        return False
    if max_boost is None:
        return False
    if max_boost > -_atmospheric_psi + 0.5:
        return False
    return turbine_limit is None or abs(turbine_limit) < 1e-6


def aspiration_label(aspiration_type: int | None, max_boost: float | None = None,
                     turbine_limit: float | None = None, sc_boost: float | None = None,
                     sc_ceiling: float | None = None) -> str:
    if is_supercharged(sc_boost, sc_ceiling):
        return 'Supercharged'
    if is_naturally_aspirated(max_boost, turbine_limit):
        return 'Naturally aspirated'
    kind = ASPIRATION_KIND.get(aspiration_type)
    if kind == 'twin':
        return 'Twin turbo'
    if kind == 'supercharger':
        return 'Supercharged'
    if kind == 'single':
        return 'Turbocharged'
    return 'Unknown'
