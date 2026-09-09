# SDRScout: Tactical Signal Diagnostic & Radio Toolkit

SDRScout is a 1-click tactical terminal HUD and signal diagnostic engine built for the Nooelec NESDR SMArt v5 and amateur radio operations. It provides zero-typing single-key hardware benchmarking, live ASCII RF noise floor metering, thermal drift testing, repeater frequency management, live plain-English output analysis, and instant application launching.

---

## 1-Key Operational Matrix

| Key | Action | Description |
| :--- | :--- | :--- |
| `[Up]` / `[Down]` | Scroll Station List | Moves cursor up/down through repeater channels, highlights active row, and copies frequency to clipboard. |
| `[N]` / `[P]` | Next / Previous Station | Ergonomic single-hand alternative to arrow keys for switching channels. |
| `[T]` | Live Audio Stream (Toggle) | Pipes `rtl_fm` demod directly into `ffplay` to listen to the selected channel in real-time. Press again to mute. |
| `[S]` | ARES Hot Carrier Scan | Sweeps 10 local repeaters for active voice traffic. Snaps cursor to whichever channel breaks squelch. |
| `[Space]` | Copy Frequency | Copies the active channel's frequency directly to Windows clipboard with click chime. |
| `[1]` | Hardware & Gain Audit | Verifies USB throughput at 2.4 MSPS, checks 29 tuner gain steps, tests for dropped samples. |
| `[2]` | PPM Thermal Drift | Benchmarks crystal oscillator error over time, confirming 0.5 PPM TCXO frequency stability. |
| `[3]` | NOAA Weather Radio Check | Tunes to 162.550 MHz (Mobile/Gulf Coast) to verify RF front-end and antenna matching. |
| `[4]` | ADS-B Aircraft Scout | Intercepts commercial Mode-S aircraft transponder frames on 1090 MHz. |
| `[5]` | Audio Logger (REC Toggle) | Records active repeater/ARES channel audio to timestamped `.raw` audio in `recordings/`. |
| `[6]` | Toggle RTL-TCP Server | Starts or stops a network streaming server (`0.0.0.0:1234`) for SDR++, SDR Console, or Kali. |
| `[7]` | Launch SDR Console V3 | Opens SDR Console V3 with a single keypress. |
| `[8]` | Launch SDR++ | Opens SDR++ with a single keypress. |
| `[Q]` | Clean Exit | Shuts down background streams and terminates cleanly. |

---

## Supercharged Tactical Telemetry

- **Live Real-Time Audio Demodulation**: Pipes `rtl_fm.exe` S16LE raw audio samples into `ffplay.exe` without window popups, outputting audio cleanly through laptop speakers or FxSound.
- **Smart Non-Blocking RF Poller**: A background worker thread polls 2-meter band noise energy every 3.5 seconds, dynamically updating the ASCII meter without interfering with active tests or audio playback.
- **ARES Priority Activity Scanner**: Rapidly measures power across local emergency repeaters and locks onto active carrier transmissions with audio prompt alerts.
- **Visual Anti-Freeze Heartbeat**: Real-time rotating ASCII radar pulse (`|`, `/`, `-`, `\`) in the header confirms the Python event loop and USB driver have not frozen.
- **Color-Coded Overload Protection**: Automatically detects when tuner gain exceeds 20.0 dB to prevent front-end clipping and harmonic distortion.
