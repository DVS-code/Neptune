"""The player's lamps: input flags, the lamp-program graph, and lamp material colours.

Three independent levers, all plain memory writes on the entity or its render data:

* **Input flags** (`Lighting.*`) - single bytes the light evaluator reads every frame.
* **Lamp programs** - every lamp channel is a short program over 15 input registers.
  Rewriting one operand (say "hazard" -> "engine running") re-wires a channel without
  touching the game's own flag, which is how signals are driven: their flags are rewritten
  by the game every frame, while program operands are plain data that hold. The channel's
  `SLEW` is the game's own output smoothing, and is how fades are done.
* **Lamp materials** - the emitter shader's constant buffer carries the lamp's RGB tint and
  the channel that lights it. Writing the GPU-mapped copy recolours the lamp on the next frame.

Everything here is pure address logic; the Lighting feature owns state and restore.
"""

from __future__ import annotations

import struct
import threading
import time
from dataclasses import dataclass, field

from neptune.memory import offsets as O
from neptune.memory.process import Process

HEAP_MIN = 0x10000000
HEAP_MAX = 0x7FF000000000
SCAN_CHUNK = 0x1000000


def _heap(value: int | None) -> bool:
    return value is not None and HEAP_MIN <= value < HEAP_MAX and value % 8 == 0


def _q(process: Process, address: int) -> int:
    return process.pointer(address) or 0


# --------------------------------------------------------------------------- input flags


def read_flag(process: Process, entity: int, offset: int) -> int | None:
    data = process.read(entity + offset, 1)
    return data[0] if data else None


def write_flag(process: Process, entity: int, offset: int, value: int) -> bool:
    return process.write(entity + offset, bytes([1 if value else 0]))


def engine_running(process: Process, entity: int) -> bool:
    return read_flag(process, entity, O.Lighting.ENGINE) == 1


def is_player(process: Process, entity: int) -> bool:
    return process.u32(entity + O.Lighting.CAR_MODE) == 0


# --------------------------------------------------------------------------- lamp programs


@dataclass
class Instruction:
    address: int  # of the 24-byte payload
    op: int
    dst: int
    a: int
    b: int
    c: int
    imm: float

    def operand(self, field_offset: int) -> int:
        return {
            O.LampInstruction.A: self.a,
            O.LampInstruction.B: self.b,
            O.LampInstruction.C: self.c,
        }[field_offset]


@dataclass
class Controller:
    channel: int
    address: int
    slew: float
    instructions: list[Instruction] = field(default_factory=list)


@dataclass
class LampGraph:
    """A snapshot of the player's lamp-controller graph, taken once per car/scene."""

    sentinel: int
    controllers: dict[int, Controller]

    @property
    def signature(self) -> tuple:
        return (
            self.sentinel,
            tuple(sorted((c.channel, c.address) for c in self.controllers.values())),
        )

    def channels(self) -> list[int]:
        return sorted(self.controllers)


def _walk(process: Process, sentinel: int, limit: int) -> list[int] | None:
    nodes, node = [], _q(process, sentinel)
    while node != sentinel:
        if not _heap(node) or node in nodes or len(nodes) >= limit:
            return None
        nodes.append(node)
        node = _q(process, node)
    return nodes


def read_graph(process: Process, entity: int) -> LampGraph | None:
    sentinel = _q(process, entity + O.Lighting.CONTROLLERS)
    count = _q(process, entity + O.Lighting.CONTROLLER_COUNT)
    if not _heap(sentinel) or not 0 < count <= O.LampController.MAX_COUNT:
        return None
    nodes = _walk(process, sentinel, O.LampController.MAX_COUNT)
    if nodes is None or len(nodes) != count:
        return None
    controllers: dict[int, Controller] = {}
    for node in nodes:
        address = _q(process, node + O.LampController.NODE_DATA)
        header = process.read(address, 0x38) if _heap(address) else None
        if not header:
            return None
        channel, slew = struct.unpack_from("<If", header, O.LampController.OUTPUT)
        if channel > O.LAMP_CHANNEL_MAX or channel in controllers:
            return None
        program_sentinel = struct.unpack_from("<Q", header, O.LampController.INSTRUCTIONS)[0]
        program_count = struct.unpack_from("<Q", header, O.LampController.INSTRUCTION_COUNT)[0]
        controller = Controller(channel, address, slew)
        if program_count:
            if not _heap(program_sentinel) or program_count > O.LampInstruction.MAX_COUNT:
                return None
            program = _walk(process, program_sentinel, O.LampInstruction.MAX_COUNT)
            if program is None or len(program) != program_count:
                return None
            for instruction_node in program:
                payload = instruction_node + O.LampController.NODE_DATA
                raw = process.read(payload, O.LampInstruction.SIZE)
                if not raw:
                    return None
                op, dst, a, b, c = struct.unpack_from("<Iiiii", raw, 0)
                imm = struct.unpack_from("<f", raw, O.LampInstruction.IMM)[0]
                controller.instructions.append(Instruction(payload, op, dst, a, b, c, imm))
        controllers[channel] = controller
    return LampGraph(sentinel, controllers)


def graph_still_current(process: Process, entity: int, graph: LampGraph) -> bool:
    """Cheap identity check: same list head and the same controller at each sampled node."""
    if _q(process, entity + O.Lighting.CONTROLLERS) != graph.sentinel:
        return False
    for controller in list(graph.controllers.values())[:4]:
        if process.u32(controller.address + O.LampController.OUTPUT) != controller.channel:
            return False
    return True


@dataclass(frozen=True)
class Remap:
    """Within `channels`, read register `target` wherever the stock program reads `source`."""

    channels: frozenset[int]
    source: int
    target: int


def plan_operands(graph: LampGraph, remaps: list[Remap]) -> dict[int, int]:
    """Operand address -> desired register, computed from the STOCK program (`graph`).

    Later remaps win when two rules claim the same operand. Only operands the opcode actually
    reads are considered, so an unused field that happens to hold an index is never touched.
    """
    desired: dict[int, int] = {}
    for remap in remaps:
        for channel in remap.channels:
            controller = graph.controllers.get(channel)
            if controller is None:
                continue
            for instruction in controller.instructions:
                for field_offset in O.LampInstruction.OPERANDS.get(instruction.op, ()):
                    if instruction.operand(field_offset) == remap.source:
                        desired[instruction.address + field_offset] = remap.target
    return desired


def stock_operands(graph: LampGraph) -> dict[int, int]:
    """Operand address -> stock register for every operand in the graph."""
    stock = {}
    for controller in graph.controllers.values():
        for instruction in controller.instructions:
            for field_offset in O.LampInstruction.OPERANDS.get(instruction.op, ()):
                stock[instruction.address + field_offset] = instruction.operand(field_offset)
    return stock


def write_operand(process: Process, address: int, register: int) -> bool:
    return process.write(address, struct.pack("<i", int(register)))


def read_operand(process: Process, address: int) -> int | None:
    return process.i32(address)


def write_slew(process: Process, controller: Controller, seconds: float) -> bool:
    return process.set_f32(controller.address + O.LampController.SLEW, max(0.0, float(seconds)))


def channel_value(process: Process, controller: Controller) -> float | None:
    return process.f32(controller.address + O.LampController.VALUE)


# --------------------------------------------------------------------------- GPU upload pages

_upload_cache: dict[int, int] = {}


def _upload_backend(process: Process, global_address: int) -> int:
    service = _q(process, global_address)
    backend = _q(process, service + O.GpuUpload.SERVICE_TO_BACKEND) if _heap(service) else 0
    if (
        _heap(backend)
        and _q(process, backend + O.GpuUpload.PAGE_SIZE) == O.GpuUpload.EXPECTED_PAGE_SIZE
    ):
        return backend
    return 0


def resolve_upload_global(process: Process) -> int:
    """Absolute address of the upload-allocator global, by signature, else the known RVA."""
    cached = _upload_cache.get(process.pid)
    if cached:
        return cached
    resolved = 0
    try:
        from neptune.memory.scanner import ModuleImage

        image = ModuleImage.capture(process)
        for pattern in O.GpuUpload.SIGS:
            hits = image.find_all(pattern, limit=2)
            if len(hits) != 1:
                continue
            displacement = process.read(hits[0] + O.GpuUpload.SIG_DISP, 4)
            if not displacement:
                continue
            target = (
                hits[0]
                + O.GpuUpload.SIG_INSN_LEN
                + int.from_bytes(displacement, "little", signed=True)
            )
            if _upload_backend(process, target):
                resolved = target
                break
    except Exception:
        resolved = 0
    if not resolved and _upload_backend(process, process.base + O.GpuUpload.GLOBAL_FALLBACK):
        resolved = process.base + O.GpuUpload.GLOBAL_FALLBACK
    if resolved:
        _upload_cache[process.pid] = resolved
    return resolved


def mapped_address(process: Process, backend: int, gpu: int, size: int) -> int:
    """CPU-visible address of a GPU constant buffer, or 0 if it is not in an upload page."""
    page_size = _q(process, backend + O.GpuUpload.PAGE_SIZE)
    pages = _q(process, backend + O.GpuUpload.PAGES)
    pages_end = _q(process, backend + O.GpuUpload.PAGES_END)
    gpu_base = _q(process, backend + O.GpuUpload.GPU_BASE)
    if not (page_size and _heap(pages) and pages_end > pages and gpu >= gpu_base):
        return 0
    relative = gpu - gpu_base
    index, offset = divmod(relative, page_size)
    if index >= (pages_end - pages) // O.GpuUpload.PAGE_STRIDE or offset + size > page_size:
        return 0
    base = _q(process, pages + index * O.GpuUpload.PAGE_STRIDE + O.GpuUpload.PAGE_MAPPED)
    return base + offset if _heap(base) else 0


# --------------------------------------------------------------------------- lamp materials


@dataclass
class LampMaterial:
    channel: int  # the channel the authored material lights from
    model: str  # e.g. "headlightl_a.modelbin"
    name: str
    layout: tuple[int, int, int]
    cpu: int
    mapped: int
    stock_rgb: tuple[float, float, float]

    @property
    def size(self) -> int:
        return self.layout[0]

    @property
    def tint_offset(self) -> int:
        return self.layout[1]

    @property
    def channel_offset(self) -> int:
        return self.layout[2]


def pose(process: Process, entity: int) -> int:
    proxy = _q(process, entity + O.Render.ENTITY_TO_PROXY)
    model = _q(process, proxy + O.Render.PROXY_TO_MODEL) if _heap(proxy) else 0
    scene = _q(process, model + O.Render.MODEL_TO_SCENE) if _heap(model) else 0
    return _q(process, scene + O.Render.SCENE_TO_POSE) if _heap(scene) else 0


def _cstring(process: Process, address: int, limit: int = 300) -> str:
    data = process.read(address, limit) if _heap(address) else None
    return data.split(b"\0")[0].decode(errors="replace") if data else ""


def _std_string(process: Process, address: int) -> str:
    raw = process.read(address, 0x20)
    if not raw:
        return ""
    length, capacity = struct.unpack_from("<QQ", raw, 0x10)
    if length > 255:
        return ""
    if capacity >= 16:
        return _cstring(process, struct.unpack_from("<Q", raw, 0)[0], length + 1)
    return raw[:length].decode(errors="replace")


LAMP_WORDS = ("light", "lamp", "marker", "bulb", "lbk", "emit", "drl", "signal", "indicator", "led")


def _looks_like_lamp(model: str, name: str) -> bool:
    """Buffer layouts alone admit coincidences (a plate or trim buffer can fit the lens
    layout), so the authored model or material name must also say it is a lamp."""
    text = f"{model} {name}".lower()
    return any(word in text for word in LAMP_WORDS)


def _same_except_owned(mapped: bytes, cpu: bytes, layout: tuple[int, int, int]) -> bool:
    """The mapped copy equals its CPU source outside the tint and channel Neptune may own,
    so a lamp already recoloured (by an earlier session or a crash) is still recognised."""
    if len(mapped) != len(cpu):
        return False
    _, tint, channel = layout
    owned = set(range(tint, tint + 12)) | set(range(channel, channel + 4))
    return all(mapped[i] == cpu[i] for i in range(len(cpu)) if i not in owned)


def _match_layout(buffer: bytes) -> tuple[int, int, int] | None:
    for layout in O.LampShader.LAYOUTS:
        size, tint, channel_offset = layout
        if len(buffer) != size:
            continue
        channel = struct.unpack_from("<I", buffer, channel_offset)[0]
        rgb = struct.unpack_from("<3f", buffer, tint)
        if channel <= O.LAMP_CHANNEL_MAX and all(0.0 <= v <= 64.0 for v in rgb):
            return layout
    return None


def find_pose_instances(process: Process, entity: int, deadline: float) -> list[int]:
    """Model instances owned by this car's pose, found near its animation component."""
    target = pose(process, entity)
    anchor = _q(process, entity + O.Render.ENTITY_TO_ANIMATION)
    if not _heap(target) or not _heap(anchor):
        return []
    needle = struct.pack("<Q", target)
    low = (anchor & ~0xFFFF) - O.Render.SEARCH_RADIUS
    high = (anchor & ~0xFFFF) + O.Render.SEARCH_RADIUS
    found: set[int] = set()
    for region, length in process.writable_regions(limit=1 << 40):
        start, end = max(region, low), min(region + length, high)
        while start < end:
            if time.monotonic() > deadline:
                return sorted(found)
            size = min(SCAN_CHUNK, end - start)
            data = process.read(start, size)
            if data:
                index = data.find(needle)
                while index >= 0:
                    if index % 8 == 0 and index >= O.ModelInstance.POSE:
                        found.add(start + index - O.ModelInstance.POSE)
                    index = data.find(needle, index + 8)
            start += size
    return sorted(found)


def find_lamp_materials(process: Process, entity: int, timeout: float = 4.0) -> list[LampMaterial]:
    """Every lamp emitter/lens material on the player's car, with its GPU-mapped buffer."""
    deadline = time.monotonic() + timeout
    upload = resolve_upload_global(process)
    backend = _upload_backend(process, upload) if upload else 0
    if not backend:
        return []
    materials: list[LampMaterial] = []
    seen: set[int] = set()
    for instance in find_pose_instances(process, entity, deadline):
        resource = _q(process, instance + O.ModelInstance.RESOURCE)
        path = (
            _cstring(process, _q(process, resource + O.ModelInstance.RESOURCE_PATH))
            if _heap(resource)
            else ""
        )
        if not path.lower().endswith(".modelbin"):
            continue
        definition = _q(process, instance + O.ModelInstance.DEFINITION)
        table = _q(process, definition + O.ModelInstance.MATERIALS) if _heap(definition) else 0
        count = _q(process, definition + O.ModelInstance.MATERIAL_COUNT) if _heap(definition) else 0
        if not _heap(table) or not 0 < count < 256:
            continue
        model = path.replace("/", "\\").split("\\")[-1]
        for row in range(count):
            record = table + row * O.ModelInstance.ROW_SIZE
            material = _q(process, record + O.ModelInstance.ROW_MATERIAL)
            if not _heap(material) or material in seen:
                continue
            compiled = _q(process, material + O.ModelInstance.MATERIAL_COMPILED)
            cpu = _q(process, compiled + O.ModelInstance.COMPILED_CPU) if _heap(compiled) else 0
            cpu_end = (
                _q(process, compiled + O.ModelInstance.COMPILED_CPU_END) if _heap(compiled) else 0
            )
            size = cpu_end - cpu
            if not _heap(cpu) or size not in (layout[0] for layout in O.LampShader.LAYOUTS):
                continue
            buffer = process.read(cpu, size)
            layout = _match_layout(buffer) if buffer else None
            if layout is None:
                continue
            name = _std_string(process, record + O.ModelInstance.ROW_NAME)
            if not _looks_like_lamp(model, name):
                continue
            binding = _q(process, compiled + O.ModelInstance.COMPILED_BINDING)
            gpu = _q(process, binding + O.ModelInstance.BINDING_GPU) if _heap(binding) else 0
            mapped = mapped_address(process, backend, gpu, size) if gpu else 0
            # The mapped copy must match its CPU source, or this is not the buffer the
            # renderer is drawing this material from.
            current = process.read(mapped, size) if mapped else None
            if not current or not _same_except_owned(current, buffer, layout):
                continue
            seen.add(material)
            # Stock values come from the CPU copy: the game never rewrites it, Neptune never
            # writes it, so it is the authored colour even if the mapped copy was recoloured.
            channel = struct.unpack_from("<I", buffer, layout[2])[0]
            rgb = struct.unpack_from("<3f", buffer, layout[1])
            materials.append(LampMaterial(channel, model, name, layout, cpu, mapped, rgb))
    return materials


def material_current(process: Process, material: LampMaterial) -> bool:
    """The CPU source still holds the authored channel, i.e. the scene was not rebuilt."""
    data = process.read(material.cpu, material.size)
    if not data:
        return False
    return (
        struct.unpack_from("<I", data, material.channel_offset)[0] == material.channel
        and struct.unpack_from("<3f", data, material.tint_offset) == material.stock_rgb
    )


def write_tint(process: Process, material: LampMaterial, rgb) -> bool:
    values = [max(0.0, min(O.LampShader.MAX_CHANNEL_VALUE, float(v))) for v in rgb]
    return process.write(material.mapped + material.tint_offset, struct.pack("<3f", *values))


def write_material_channel(process: Process, material: LampMaterial, channel: int) -> bool:
    return process.write(material.mapped + material.channel_offset, struct.pack("<I", int(channel)))


def read_material_channel(process: Process, material: LampMaterial) -> int | None:
    return process.u32(material.mapped + material.channel_offset)


def read_tint(process: Process, material: LampMaterial) -> tuple[float, float, float] | None:
    data = process.read(material.mapped + material.tint_offset, 12)
    return struct.unpack("<3f", data) if data else None


class MaterialScan:
    """Runs the (sub-second) material discovery off the tick thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._result: tuple[int, int, list[LampMaterial]] | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, process: Process, entity: int, scene: int) -> bool:
        if self.busy:
            return False

        def work():
            try:
                found = find_lamp_materials(process, entity)
            except Exception:
                found = []
            with self._lock:
                self._result = (entity, scene, found)

        self._thread = threading.Thread(target=work, name="neptune-lamp-scan", daemon=True)
        self._thread.start()
        return True

    def take(self) -> tuple[int, int, list[LampMaterial]] | None:
        with self._lock:
            result, self._result = self._result, None
        return result
