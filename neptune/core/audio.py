"""Sound playback for feature modules.

Two very different jobs, so two classes:

  `OneShot`  — a short effect fired on an event (the air-ride release hiss). Uses QSoundEffect,
               which keeps the sample decoded in memory and can start instantly. WAV only.
  `Loop`     — a bed that plays continuously while a feature is enabled (the Maybach bounce track).
               Uses QMediaPlayer, which handles MP3 and can loop forever.

Both are deliberately FORGIVING: if the asset is missing, the audio backend is unavailable, or the
platform has no output device, every call turns into a no-op instead of raising. Sound is a garnish
on these features — a missing file must never stop the car from moving.

⚠️ Qt objects must be created on the GUI thread. Playback is triggered from feature code that may be
running on the runtime thread, so `play()`/`start()` are safe to call from anywhere: they marshal
onto the owning thread with a queued invocation rather than touching the player directly.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QMetaObject, QUrl, Slot

from neptune.core import paths

MAX_VOLUME = 100


def _clamp_volume(percent) -> float:
    try:
        value = float(percent)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value / MAX_VOLUME))


class OneShot(QObject):
    """A short sample played on demand (air-ride release)."""

    def __init__(self, filename: str, parent=None):
        super().__init__(parent)
        self._effect = None
        path = paths.asset(filename)
        if not path:
            return
        try:
            from PySide6.QtMultimedia import QSoundEffect
            effect = QSoundEffect(self)
            effect.setSource(QUrl.fromLocalFile(path))
            self._effect = effect
        except Exception:
            self._effect = None

    @property
    def available(self) -> bool:
        return self._effect is not None

    def set_volume(self, percent) -> None:
        if self._effect is not None:
            try:
                self._effect.setVolume(_clamp_volume(percent))
            except Exception:
                pass

    def play(self) -> None:
        """Fire the sample. Safe to call from any thread."""
        if self._effect is None:
            return
        QMetaObject.invokeMethod(self, '_play', Qt.QueuedConnection)

    @Slot()
    def _play(self) -> None:
        """Fire the sample, waiting for the decode if it has not finished yet.

        ⚠️ QSoundEffect loads ASYNCHRONOUSLY — `isLoaded()` is False for roughly a second after
        construction. Simply skipping the play when it is not loaded silently swallowed the very
        first air-ride drop after launch, which is exactly the moment a user tests it. If the sample
        is still decoding, arm a one-shot that plays as soon as it becomes ready.
        """
        effect = self._effect
        if effect is None:
            return
        try:
            from PySide6.QtMultimedia import QSoundEffect
            if effect.isLoaded():
                effect.play()
                return
            if effect.status() == QSoundEffect.Error:
                return

            def when_ready():
                try:
                    if effect.isLoaded():
                        effect.statusChanged.disconnect(when_ready)
                        effect.play()
                except Exception:
                    pass

            effect.statusChanged.connect(when_ready)
        except Exception:
            pass


class Loop(QObject):
    """A track that repeats for as long as a feature is switched on (Maybach bounce)."""

    def __init__(self, filename: str, parent=None):
        super().__init__(parent)
        self._player = None
        self._output = None
        self._volume = 0.7
        path = paths.asset(filename)
        if not path:
            return
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            player = QMediaPlayer(self)
            output = QAudioOutput(self)
            player.setAudioOutput(output)
            player.setSource(QUrl.fromLocalFile(path))


            player.setLoops(QMediaPlayer.Infinite)
            output.setVolume(self._volume)
            self._player, self._output = player, output
        except Exception:
            self._player = self._output = None

    @property
    def available(self) -> bool:
        return self._player is not None

    def set_volume(self, percent) -> None:
        self._volume = _clamp_volume(percent)
        if self._output is not None:
            try:
                self._output.setVolume(self._volume)
            except Exception:
                pass

    def start(self) -> None:
        if self._player is None:
            return
        QMetaObject.invokeMethod(self, '_start', Qt.QueuedConnection)

    def stop(self) -> None:
        if self._player is None:
            return
        QMetaObject.invokeMethod(self, '_stop', Qt.QueuedConnection)

    @Slot()
    def _start(self) -> None:
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            if self._player.playbackState() != QMediaPlayer.PlayingState:
                self._player.play()
        except Exception:
            pass

    @Slot()
    def _stop(self) -> None:
        try:
            self._player.stop()
        except Exception:
            pass
