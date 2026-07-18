# ZDX Parallel Pyxel VM GUI Builds

The desktop interface is designed for three operating systems:

## Windows

Target:
- Windows 10/11
- packaged executable via PyInstaller

Build:

```bash
pyinstaller --onefile --windowed zdx_gui.py
```

## Linux

Target:
- Ubuntu/Debian
- Fedora based systems

Dependencies:

```bash
python3-tk
```

Build:

```bash
pyinstaller --onefile --windowed zdx_gui.py
```

## macOS

Target:
- Intel Macs
- Apple Silicon

Build:

```bash
pyinstaller --onefile --windowed zdx_gui.py
```

## Future GUI Modules

Planned:

- node status dashboard
- peer list
- frame synchronization monitor
- VM execution controls
- network diagnostics

Developed by ZeroDriveX LLC.
