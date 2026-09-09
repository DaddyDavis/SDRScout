# SDRScout: Tactical Signal Diagnostic & Radio Toolkit

SDRScout is a 1-click tactical terminal HUD and signal diagnostic engine built for the Nooelec NESDR SMArt v5 and amateur radio operations. It provides zero-typing single-key hardware benchmarking, thermal drift testing, repeater frequency management, live plain-English output analysis, and instant application launching.

---

## 1-Key Operational Matrix

| Key | Action | Description |
| :--- | :--- | :--- |
| `[1]` | Hardware & Gain Audit | Verifies USB throughput, checks 29 tuner gain steps, tests for dropped samples. |
| `[2]` | PPM Thermal Drift | Benchmarks crystal oscillator error for TCXO frequency stability. |
| `[3]` | NOAA Weather Radio Check | Tunes to 162.550 MHz (Mobile/Gulf Coast) to verify RF front-end and antenna matching. |
| `[4]` | ADS-B Aircraft Scout | Intercepts Mode-S commercial aircraft transponder frames on 1090 MHz. |
| `[6]` | Toggle RTL-TCP Server | Starts or stops a network streaming server (`0.0.0.0:1234`) for SDR++, SDR Console, or Kali. |
| `[7]` | Launch SDR Console V3 | Opens SDR Console V3. |
| `[8]` | Launch SDR++ | Opens SDR++. |
| `[0]-[9]` | Auto-Copy Frequency | Instantly copies selected Lucedale / George County ARES repeater frequency to clipboard. |
| `[Q]` | Clean Exit | Shuts down background streams and terminates cleanly. |

---

## Plain-English Output Analysis Engine

Rather than dumping raw hexadecimal packets and buffer error codes, SDRScout parses live low-level telemetry and translates it into operational status:
- USB bus throughput health and buffer overrun detection.
- Gain recommendations based on active band and station type.
- TCXO crystal stability and drift guidance for satellite tracking.
- Driver lock alerts if Oracle VirtualBox or other software has claimed the USB filter.
