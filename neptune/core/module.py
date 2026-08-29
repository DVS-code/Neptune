"""The feature-module contract and the registry that drives it."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from neptune.vehicle.vehicle import Vehicle


class FeatureModule:
    """Base class for every feature. Subclasses override only what they need."""

    name = 'unnamed'
    title = 'Unnamed'
    subtitle = ''
    icon = ''
    group = 'Vehicle'
    order = 100
    ticks = True
    always_refresh = False

    def __init__(self):
        self.vehicle: Vehicle | None = None
        self.settings = None

    def on_attach(self, vehicle: Vehicle) -> None:
        """A car became available. Capture stock values here."""
        self.vehicle = vehicle

    def on_detach(self) -> None:
        """The car or process went away. Never write memory here."""
        self.vehicle = None

    def tick(self, vehicle: Vehicle) -> None:
        """Per-frame work while a car exists. Must be cheap and must not raise."""

    def tick_process(self, process) -> None:
        """Per-frame work that needs only the process, dispatched with no car loaded."""

    def on_car_changed(self, vehicle: Vehicle) -> None:
        """A different car was loaded. Drop the tune and re-capture stock."""
        self.on_attach(vehicle)

    def on_car_reloaded(self, vehicle: Vehicle) -> None:
        """The same car reloaded. Re-apply anything this module was holding."""

    def restore(self) -> None:
        """Undo every change this module made to the game."""

    def reset_controls(self) -> None:
        """Return this module's controls to their neutral state."""

    def shutdown(self) -> None:
        """Release any resource that outlives the window."""

    def build_page(self, page) -> None:
        """Populate this module's page."""

    def refresh(self, vehicle: Vehicle | None) -> None:
        """Update live readouts. Runs on the interface thread."""

    def save_state(self) -> dict:
        """Serialise user-facing settings for a preset."""
        return {}

    def load_state(self, data: dict) -> None:
        """Apply settings from a preset. Must tolerate missing keys."""

    def bindings(self) -> list[dict]:
        """Keybinds this module owns, for the settings page."""
        return []


class ModuleRegistry:
    """Holds registered modules and fans lifecycle events out to them."""

    def __init__(self):
        self._modules: list[FeatureModule] = []
        self._errors: list[str] = []

    def register(self, module: FeatureModule) -> FeatureModule:
        self._modules.append(module)
        self._modules.sort(key=lambda item: (item.order, item.name))
        return module

    def __iter__(self):
        return iter(self._modules)

    def __len__(self) -> int:
        return len(self._modules)

    def get(self, name: str) -> FeatureModule | None:
        for module in self._modules:
            if module.name == name:
                return module
        return None

    def dispatch(self, event: str, *args) -> None:
        """Call `event` on every module, isolating failures."""
        for module in self._modules:
            if event in ('tick', 'tick_process') and not module.ticks:
                continue
            handler = getattr(module, event, None)
            if handler is None:
                continue
            try:
                handler(*args)
            except Exception as error:
                self._errors.append(f'{module.title}: {error}')

    def take_errors(self) -> list[str]:
        errors, self._errors = self._errors, []
        return errors
