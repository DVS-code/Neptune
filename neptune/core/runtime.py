"""Owns the process, the current vehicle and the tick loop."""
from __future__ import annotations

import threading
import time

from neptune.core.module import ModuleRegistry
from neptune.memory import offsets as O
from neptune.memory.process import Process, ProcessError
from neptune.vehicle.vehicle import Vehicle, find_vehicles

TICK_SECONDS = 0.01
RESCAN_SECONDS = 1.0
IDENTITY_GRACE_SECONDS = 4.0

STATE_DETACHED = 'detached'
STATE_WAITING = 'waiting'
STATE_READY = 'ready'


class Runtime:
    """Drives the module registry against the running game."""

    def __init__(self, registry: ModuleRegistry):
        self.registry = registry
        self.process: Process | None = None
        self.vehicle: Vehicle | None = None

        self._identity = None
        self._identity_lost_at = 0.0
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_scan = 0.0
        self._state = STATE_DETACHED
        self._message = 'Not attached'
        self._lock = threading.Lock()

    @property
    def status(self) -> tuple[str, str]:
        with self._lock:
            return self._state, self._message

    @property
    def attached(self) -> bool:
        return self.process is not None and self.process.alive

    @property
    def has_car(self) -> bool:
        return self.vehicle is not None

    def _set_status(self, state: str, message: str) -> None:
        with self._lock:
            self._state = state
            self._message = message

    def attach(self) -> tuple[bool, str]:
        if self.attached:
            return True, self._message
        try:
            self.process = Process.attach(O.GAME_EXE)
        except ProcessError as error:
            self._set_status(STATE_DETACHED, str(error))
            return False, str(error)
        self._set_status(STATE_WAITING, 'Attached. Waiting for a car.')
        self._acquire()
        self.start()
        return True, self._message

    def detach(self) -> None:
        self.stop()
        self.registry.dispatch('restore')
        self.registry.dispatch('on_detach')
        self.vehicle = None
        self._identity = None
        if self.process:
            self.process.close()
            self.process = None
        self._set_status(STATE_DETACHED, 'Not attached')

    def start(self) -> None:
        if self._thread:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name='neptune-runtime')
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        thread = self._thread
        self._thread = None
        if thread:
            thread.join(timeout=1.0)

    def restore_all(self) -> None:
        self.registry.dispatch('restore')
        self.registry.dispatch('reset_controls')

    def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception as error:
                self.registry.take_errors()
                self._set_status(self._state, f'Runtime error: {error}')
            time.sleep(TICK_SECONDS)

    def _tick(self) -> None:
        if not self.process:
            return

        if not self.process.alive:
            self.vehicle = None
            self._identity = None
            self.registry.dispatch('on_detach')
            self.process.close()
            self.process = None
            self._set_status(STATE_DETACHED, 'The game closed.')
            return

        now = time.monotonic()
        self.registry.dispatch('tick_process', self.process)

        if self.vehicle is None:
            if now - self._last_scan >= RESCAN_SECONDS:
                self._last_scan = now
                self._acquire()
            return

        if not self.vehicle.is_car():
            self.vehicle = None
            self._identity_lost_at = now
            self.registry.dispatch('on_detach')
            self._set_status(STATE_WAITING, 'Waiting for a car.')
            return

        if now - self._last_scan >= RESCAN_SECONDS:
            self._last_scan = now
            self._acquire()
            if self.vehicle is None:
                return

        self.registry.dispatch('tick', self.vehicle)

    def _car_was_lost(self) -> bool:
        """True when the vehicle actually went away and has now come back.

        Distinguishes a genuine reload (respawn, fast travel, a race start) from the car simply
        still being there on the next rescan. Only the former should re-apply held state.
        """
        return self._identity_lost_at > 0.0

    def _acquire(self) -> None:
        """Find the player's car and tell the modules what changed."""
        if not self.process:
            return

        vehicles, error = find_vehicles(self.process)
        if error or not vehicles:
            if self.vehicle is not None:
                self.vehicle = None
                self._identity_lost_at = time.monotonic()
                self.registry.dispatch('on_detach')
            self._set_status(STATE_WAITING, error or 'Waiting for a car.')
            return

        vehicle = vehicles[0]
        identity = vehicle.fingerprint()
        previous = self._identity
        self.vehicle = vehicle

        if identity is None:
            self._set_status(STATE_READY, 'Car ready')
            return

        self._identity = identity

        if previous is None:
            self.registry.dispatch('on_attach', vehicle)
            self._set_status(STATE_READY, 'Car ready')
            return

        if identity == previous:


            if self._car_was_lost():
                self._identity_lost_at = 0.0
                self.registry.dispatch('on_car_reloaded', vehicle)
            self._set_status(STATE_READY, 'Car ready')
            return

        self.registry.dispatch('on_car_changed', vehicle)
        self._set_status(STATE_READY, 'New car detected. Tune reset.')
