# Neptune

A live tuning tool for Forza Horizon 6 on PC.

Neptune connects to the running game and lets you change how your car behaves
while you drive: torque delivery and boost.

## Features

| Tab | What it does |
|---|---|
| **Engine** | Reshape the torque curve by dragging it, set a torque multiplier and rev limit, and hold anti-lag on a key to build boost off the line. |
| **Turbo** | Boost ceiling, extra torque, spool behaviour, synthetic lag, per-gear boost, and a boost map you can shape per engine speed. |
| **Suspension** | Set ride height per axle as a percentage, and drop the car on a key press with a smooth ramp. |
| **Boost Gauge** | A floating boost gauge on top of the game, as a dial, a digital readout or a bar. |
| **Dragy** | Time your car between two speeds, with an on-screen timer and recent runs. |
| **Tunes** | Save your engine and turbo setup per car and switch between saved tunes with one key while driving. |
| **Presets** | Save and reload whole setups. |
| **Settings** | Units, display and startup behaviour. |

Anti-lag, air ride and tune switching can each be bound to a key or a
controller button or a racing-wheel button, on their own tab.

## Download

Grab the latest `Neptune.exe` from the
[releases page](https://github.com/DVS-code/Neptune/releases/latest).

Start the game and drive out into the world, then run Neptune. It needs to run
**as administrator** to talk to the game, and will ask for that on launch.

Windows may warn about an unrecognised app, because the build is not
code-signed. Each release ships a `SHA256SUMS.txt` you can check against.

## Requirements

- Windows 10 or 11
- Forza Horizon 6 on PC (Steam or Microsoft Store)
- Python 3.11 or newer, if running from source

## Running from source

```
pip install -r requirements.txt
python -m neptune.app
```

Run your terminal as administrator, or Neptune will not be able to attach.

## Building a single executable

```
pip install pyinstaller
pyinstaller neptune.spec
```

The result is `dist/Neptune.exe`.

## Where your data is kept

Tunes, presets and settings are written to a `data` folder next to the
executable, so you can move the whole folder to another machine and keep them.
If that location is not writable, Neptune falls back to `%APPDATA%\Neptune`.

The exact path is shown at the bottom of the Settings tab.

## Notes

- Neptune reads and writes the game's memory only while you have it open. It
  makes no permanent changes to the game and installs nothing.
- Everything Neptune changes is put back when it closes. You can turn that off
  in Settings, and there is a **Restore everything** button at any time.

## Project layout

```
neptune/
  core/      module contract, registry, runtime loop, settings, storage
  memory/    process access, address table, pattern scanning
  vehicle/   car discovery, identity and live state
  ui/        theme, shell, reusable widgets
  features/  one file per tab
```

Adding a feature is one file in `features/` and one line in `app.py`.

## Licence

GNU General Public License v3.0. See [LICENSE](LICENSE).

If you distribute a modified version, or a build of it, you must also make the
source of your changes available under the same licence.

Neptune is an unofficial, fan-made tool. It is not affiliated with, endorsed by,
or connected to Playground Games, Turn 10 Studios, Xbox Game Studios or
Microsoft. Forza and Forza Horizon are trademarks of Microsoft Corporation.
