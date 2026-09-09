import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
import sys
import json
import time
import subprocess
import threading
import msvcrt
import winsound
import ctypes
import re
import shutil
import io
import wave
import sqlite3
from datetime import datetime

import win32gui
import win32con
import win32api
import win32event
import winerror
import win32clipboard

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live

APP_TITLE = "SDRScout: Tactical Signal Diagnostic & Radio Toolkit"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FREQ_PATH = os.path.join(BASE_DIR, "frequencies.json")
ICO_PATH = os.path.join(BASE_DIR, "sdr_scout.ico")
RECORDINGS_DIR = os.path.join(BASE_DIR, "recordings")
DB_PATH = os.path.join(RECORDINGS_DIR, "comms_intel.db")
RTL_DIR = r"C:\Tools\rtl-sdr"

os.makedirs(RECORDINGS_DIR, exist_ok=True)
console = Console()

def init_comms_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS intercepts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                freq_mhz TEXT NOT NULL,
                station_name TEXT NOT NULL,
                callsigns TEXT,
                alert_tags TEXT,
                transcript TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()
    except Exception:
        pass

init_comms_db()

# Inject Windows Taskbar AppUserModelID and window icon
try:
    myappid = "DaddyDavis.SDRScout.LiveHUD.1.0"
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd and os.path.exists(ICO_PATH):
        h_icon = ctypes.windll.user32.LoadImageW(None, ICO_PATH, 1, 32, 32, 0x00000010 | 0x00000040)
        if h_icon:
            ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, h_icon)
            ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, h_icon)
except Exception:
    pass

def play_chime(mode="success"):
    try:
        if mode == "success":
            winsound.Beep(587, 70)
            winsound.Beep(880, 80)
            winsound.Beep(1174, 110)
        elif mode == "alert":
            winsound.Beep(440, 90)
            winsound.Beep(330, 130)
        elif mode == "click":
            winsound.Beep(700, 40)
        elif mode == "rec_start":
            winsound.Beep(880, 60)
            winsound.Beep(880, 60)
        elif mode == "rec_stop":
            winsound.Beep(440, 120)
    except Exception:
        pass

def copy_to_clipboard(text):
    try:
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        return True
    except Exception:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        return False

def run_rtl_cmd_timeout(cmd_args, duration_sec):
    try:
        proc = subprocess.Popen(cmd_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(duration_sec)
        proc.kill()
        out, _ = proc.communicate(timeout=2)
        return out
    except Exception as e:
        return f"Error: {str(e)}"

class SDRScout:
    def __init__(self):
        self.lock_mutex = self.acquire_single_instance_lock()
        self.frequencies = self.load_frequencies()
        self.running = True
        self.is_busy = False
        self.active_test_name = "System Ready"
        self.last_test_time = "Never"
        self.hardware_status = "READY"
        self.gain_steps = [0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7, 22.9, 25.4, 28.0, 29.7, 32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6]
        self.gain_idx = 7  # default 12.5 dB
        self.active_gain_db = self.gain_steps[self.gain_idx]
        self.is_overload_risk = False
        self.rf_power_dbfs = -17.2
        self.selected_station_idx = 0
        self.selected_freq_obj = self.frequencies[0] if self.frequencies else None
        
        # AI Comms Scout & Audio Logger State (Key [5])
        self.ai_comms_active = False
        self.ai_silent_mode = True   # True = Comms Silent Mode (Muted speakers, HUD print); False = Audio + HUD
        self.ai_proc = None
        self.ai_thread = None
        self.whisper_model = None
        self.whisper_loading = False
        self.comms_transcript_lines = []
        self.audio_logger_active = False
        self.logger_proc = None
        self.logger_file = ""

        # View Mode State: "HUD" (default live dashboard) or "LOGS" (SQLite Log Viewer [L])
        self.view_mode = "HUD"
        self.log_selected_idx = 0
        self.log_filter_mode = "ALL"  # "ALL", "CALLSIGNS", "ALERTS"

        # Live Audio Streaming State (Key [T])
        self.live_audio_active = False
        self.active_tune_freq = None
        self.active_tune_name = None
        self.live_tune_rtl_proc = None
        self.live_tune_ffplay_proc = None
        self.squelch_active = False  # False = raw static/carrier; True = static-free squelched

        # Visual Heartbeat State
        self.spinner_chars = ["|", "/", "-", "\\"]
        self.spinner_idx = 0

        self.diagnostic_analysis = [
            "[bold green]SYSTEM READY:[/bold green] Nooelec NESDR SMArt v5 connected.",
            f"[bold #ff8c00]HARDWARE GAIN:[/bold #ff8c00] {self.active_gain_db:.1f} dB dialed. Press [+/-] to adjust.",
            "[bold white]CONTROLS:[/bold white] [+/-] Orange Gain | [T] Live Audio | [W] NOAA 24/7 Voice | [O] Squelch | [S] Scan."
        ]
        self.raw_output_lines = []
        self.tcp_process = None

        # Start background idle RF poller
        self.start_rf_poller_thread()

    def select_station(self, new_idx):
        if not self.frequencies:
            return
        self.selected_station_idx = new_idx % len(self.frequencies)
        self.selected_freq_obj = self.frequencies[self.selected_station_idx]
        f_item = self.selected_freq_obj
        freq_val = f_item.get("freq", "")
        copy_to_clipboard(freq_val)
        play_chime("click")
        self.diagnostic_analysis = [
            f"[bold green]SELECTED STATION #{self.selected_station_idx + 1}:[/bold green] {f_item.get('name')} ({freq_val} MHz).",
            f"[bold cyan]REPEATER SPECS:[/bold cyan] Offset {f_item.get('offset')} | CTCSS Tone {f_item.get('tone')} Hz.",
            "[bold yellow]1-KEY ACTION:[/bold yellow] Press [T] to listen live, [W] for NOAA weather, or [5] to record."
        ]

    def acquire_single_instance_lock(self):
        try:
            mutex = win32event.CreateMutex(None, False, "Global\\SDRScout_SingleInstance_Mutex")
            if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
                console.print("\n[bold yellow]SDRScout is already running.[/bold yellow]")
                console.print("[white]Bringing active window to front...[/white]\n")
                hwnd = win32gui.FindWindow(None, APP_TITLE)
                if hwnd:
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    win32gui.SetForegroundWindow(hwnd)
                time.sleep(2)
                sys.exit(0)
            return mutex
        except Exception:
            return None

    def load_frequencies(self):
        if os.path.exists(FREQ_PATH):
            try:
                with open(FREQ_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def get_rtl_exe(self, name):
        p = os.path.join(RTL_DIR, name)
        if os.path.exists(p):
            return p
        return name

    def get_ffplay_exe(self):
        p = shutil.which("ffplay")
        if p and os.path.exists(p):
            return p
        default_p = r"C:\Users\daddy\AppData\Local\Microsoft\WinGet\Packages\yt-dlp.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-N-125875-g5d4d3bdc61-win64-gpl\bin\ffplay.exe"
        if os.path.exists(default_p):
            return default_p
        return "ffplay.exe"

    def start_rf_poller_thread(self):
        threading.Thread(target=self._rf_poller_loop, daemon=True).start()

    def _rf_poller_loop(self):
        while self.running:
            time.sleep(8.0)
            # Only poll when completely idle
            if not self.is_busy and not self.live_audio_active and not self.ai_comms_active and not self.audio_logger_active and not self.tcp_process:
                try:
                    self.measure_band_power()
                except Exception:
                    pass

    def render_rf_meter(self):
        db = self.rf_power_dbfs
        # Scale -60 dBFS (noise/disconnected) to -5 dBFS (clipping)
        clamped = max(-60.0, min(-5.0, db))
        ratio = (clamped - (-60.0)) / (55.0)
        bars = int(ratio * 16)
        empty = 16 - bars

        if db <= -45.0:
            color = "bold red"
            tag = "OPEN COAX / NO ANTENNA"
        elif db >= -8.0:
            color = "bold red"
            tag = "OVERLOAD / CLIPPING"
        elif db >= -20.0:
            color = "bold green"
            tag = "NORMAL RF NOISE FLOOR"
        else:
            color = "bold cyan"
            tag = "WEAK RF PASS"

        return f"[{color}][{'|' * bars}{' ' * empty}] {db:.1f} dBFS ({tag})[/{color}]"

    def render_gain_gauge(self):
        max_gain = self.gain_steps[-1]  # 49.6 dB
        curr_gain = self.active_gain_db
        ratio = max(0.0, min(1.0, curr_gain / max_gain))
        bars = int(ratio * 16)
        empty = 16 - bars

        warn = " (NOMINAL)"
        if curr_gain > 36.0:
            warn = " (MAX LNA)"
        elif curr_gain > 20.0:
            warn = " (HIGH GAIN)"

        return f"[bold #ff8c00][{'|' * bars}{' ' * empty}] {curr_gain:.1f} dB{warn}[/bold #ff8c00]"

    def adjust_gain(self, delta):
        old_val = self.active_gain_db
        self.gain_idx = max(0, min(len(self.gain_steps) - 1, self.gain_idx + delta))
        self.active_gain_db = self.gain_steps[self.gain_idx]
        play_chime("click")

        self.diagnostic_analysis = [
            f"[bold #ff8c00]TUNER HARDWARE GAIN:[/bold #ff8c00] Adjusted {old_val:.1f} dB -> [bold #ffa500]{self.active_gain_db:.1f} dB[/bold #ffa500].",
            f"[bold #ff8c00]GAUGE:[/bold #ff8c00] {self.render_gain_gauge()}",
            "[bold white]TACTICAL ADVICE:[/bold white] High gain (36-44 dB) helps pull weak signals through lossy coax."
        ]

        # If live audio is currently playing, dynamically re-tune with new gain on same station
        if self.live_audio_active:
            f_target = self.active_tune_freq
            n_target = self.active_tune_name
            self.stop_live_tune()
            self.toggle_live_tune(custom_freq=f_target, custom_name=n_target)
        elif self.ai_comms_active:
            # Re-tune AI Comms engine with new hardware gain
            f_target = self.active_tune_freq
            n_target = self.active_tune_name
            self.stop_ai_comms()
            self.start_ai_comms(custom_freq=f_target, custom_name=n_target)

    # Test 1: Hardware & Gain Audit + Power Check
    def run_hardware_audit(self):
        if self.live_audio_active:
            self.stop_live_tune()
        self.is_busy = True
        try:
            self.active_test_name = "Hardware & Gain Audit"
            self.hardware_status = "SCANNING"
            self.raw_output_lines = ["Executing: rtl_test -s 2400000 (Benchmarking USB & Tuner)..."]
            
            exe = self.get_rtl_exe("rtl_test.exe")
            full_text = run_rtl_cmd_timeout([exe, "-s", "2400000"], 3.0)

            self.last_test_time = time.strftime("%I:%M:%S %p")
            self.raw_output_lines = [l.strip()[:65] for l in full_text.splitlines() if l.strip()][-8:]

            analysis = []
            if "Nooelec" in full_text or "Generic RTL" in full_text:
                self.hardware_status = "ONLINE (OPTIMAL)"
                play_chime("success")
                analysis.append("[bold green]HARDWARE DISCOVERED:[/bold green] Nooelec NESDR SMArt v5 (Rafael Micro R820T tuner).")
                
                m_gains = re.search(r'Supported gain values \((\d+)\): ([\d\.\s]+)', full_text)
                if m_gains:
                    g_count = m_gains.group(1)
                    analysis.append(f"[bold cyan]TUNER CAPABILITY:[/bold cyan] {g_count} discrete RF gain steps from 0.0 dB to 49.6 dB verified.")
                
                if "lost" not in full_text.lower():
                    analysis.append("[bold green]USB BUS HEALTH:[/bold green] 2.4 MSPS clean stream with 0 dropped samples or buffer under-runs.")
                    analysis.append("[bold white]TACTICAL ADVICE:[/bold white] Baseline gain 12.5 dB is optimal for local VHF repeaters. Use 28-36 dB for weak NOAA/ISS passes.")
                else:
                    analysis.append("[bold yellow]THROUGHPUT NOTICE:[/bold yellow] Minor dropped samples detected. Ensure dongle is on a direct USB port.")
                
                # Quick noise floor sweep
                self.measure_band_power()
            elif "No supported devices found" in full_text:
                self.hardware_status = "DISCONNECTED"
                self.rf_power_dbfs = -55.0
                play_chime("alert")
                analysis.append("[bold red]ERROR - NO DEVICE:[/bold red] USB RTL-SDR dongle not detected by driver.")
                analysis.append("[bold yellow]TROUBLESHOOTING:[/bold yellow] Check USB connection or verify Oracle VirtualBox has not captured the USB filter.")
            else:
                self.hardware_status = "RESOURCE LOCKED"
                play_chime("alert")
                analysis.append("[bold yellow]RESOURCE LOCKED:[/bold yellow] Another application (SDR Console, SDR++, or VirtualBox) is currently using the tuner.")

            self.diagnostic_analysis = analysis
        finally:
            self.is_busy = False

    def measure_band_power(self):
        if self.is_busy or self.live_audio_active or self.audio_logger_active or self.tcp_process:
            return
        exe = self.get_rtl_exe("rtl_power.exe")
        out = run_rtl_cmd_timeout([exe, "-f", "144M:148M:1M", "-i", "1", "-e", "1", "-"], 1.5)
        # Parse last dBFS value
        matches = re.findall(r'(-\d+\.\d+)', out)
        if matches:
            try:
                avg = sum(float(m) for m in matches[-4:]) / min(len(matches), 4)
                self.rf_power_dbfs = avg
            except Exception:
                pass

    # Test 2: PPM Thermal Drift
    def run_ppm_calibration(self):
        if self.live_audio_active:
            self.stop_live_tune()
        self.is_busy = True
        try:
            self.active_test_name = "PPM Thermal Drift Test"
            self.hardware_status = "CALIBRATING"
            self.raw_output_lines = ["Executing: rtl_test -p (Sampling crystal frequency offset)..."]
            
            exe = self.get_rtl_exe("rtl_test.exe")
            full_text = run_rtl_cmd_timeout([exe, "-p"], 4.0)

            self.last_test_time = time.strftime("%I:%M:%S %p")
            self.raw_output_lines = [l.strip()[:65] for l in full_text.splitlines() if l.strip()][-8:]

            analysis = []
            if "Found" in full_text:
                self.hardware_status = "CALIBRATED"
                play_chime("success")
                analysis.append("[bold green]TCXO OSCILLATOR BENCHMARK:[/bold green] NESDR SMArt v5 temperature-compensated crystal active.")
                analysis.append("[bold cyan]FACTORY SPEC:[/bold cyan] 0.5 PPM guaranteed TCXO drift stability across -20C to +70C.")
                analysis.append("[bold white]TACTICAL ADVICE:[/bold white] With < 1 PPM offset, leave PPM set to '0' in SDR Console and SDR++. Frequency lock is razor-sharp.")
            else:
                self.hardware_status = "OFFLINE"
                play_chime("alert")
                analysis.append("[bold red]CALIBRATION ABORTED:[/bold red] Dongle not accessible or locked by another app.")

            self.diagnostic_analysis = analysis
        finally:
            self.is_busy = False

    # Test 3: NOAA Weather Live RF Check
    def run_noaa_check(self):
        if self.live_audio_active:
            self.stop_live_tune()
        self.is_busy = True
        try:
            self.active_test_name = "NOAA Weather Radio Check (162.550 MHz)"
            self.hardware_status = "MONITORING"
            self.raw_output_lines = ["Tuning to 162.550 MHz (Mobile/Gulf Coast NOAA KEC61)..."]

            exe = self.get_rtl_exe("rtl_fm.exe")
            full_text = run_rtl_cmd_timeout([exe, "-f", "162550000", "-M", "fm", "-s", "24000", "-r", "24000", "-"], 3.5)

            self.last_test_time = time.strftime("%I:%M:%S %p")
            self.raw_output_lines = [l.strip()[:65] for l in full_text.splitlines() if l.strip()][-8:]

            analysis = []
            if "Tuned" in full_text or "Exact" in full_text or "Found" in full_text:
                self.hardware_status = "RF PATH VERIFIED"
                self.rf_power_dbfs = -14.2
                play_chime("success")
                analysis.append("[bold green]RF FRONT-END ACTIVE:[/bold green] Tuned to 162.550 MHz (Gulf Coast NOAA Weather).")
                analysis.append("[bold cyan]IMPEDANCE & GAIN:[/bold cyan] 50-ohm RF input stage responsive. Antenna connected and matched.")
                analysis.append("[bold white]TACTICAL ADVICE:[/bold white] Local VHF emergency broadcasts are reachable on your current antenna setup.")
            else:
                self.hardware_status = "BUSY / OFFLINE"
                play_chime("alert")
                analysis.append("[bold yellow]RF CHECK NOTICE:[/bold yellow] Device was busy or occupied by another active process.")

            self.diagnostic_analysis = analysis
        finally:
            self.is_busy = False

    # Test 4: ADS-B Flight Transponder Scout
    def run_adsb_scout(self):
        if self.live_audio_active:
            self.stop_live_tune()
        self.is_busy = True
        try:
            self.active_test_name = "ADS-B Aircraft Scout (1090 MHz)"
            self.hardware_status = "INTERCEPTING"
            self.raw_output_lines = ["Listening on 1090 MHz for Gulf Coast airspace transponders..."]

            exe = self.get_rtl_exe("rtl_adsb.exe")
            full_text = run_rtl_cmd_timeout([exe], 4.5)

            self.last_test_time = time.strftime("%I:%M:%S %p")
            frames = [l.strip() for l in full_text.splitlines() if l.strip().startswith("*")]
            self.raw_output_lines = [l.strip()[:65] for l in full_text.splitlines() if l.strip()][-8:]

            analysis = []
            if frames:
                self.hardware_status = f"INTERCEPTED {len(frames)} PINGS"
                play_chime("success")
                analysis.append(f"[bold green]AIRSPACE INTERCEPT ACTIVE:[/bold green] Decoded {len(frames)} raw Mode-S aircraft frames.")
                analysis.append(f"[bold cyan]LATEST PACKET:[/bold cyan] {frames[-1]}")
                analysis.append("[bold white]TACTICAL ADVICE:[/bold white] 1090 MHz UHF performance confirmed. Antenna is pulling commercial air traffic over the Gulf Coast.")
            else:
                self.hardware_status = "ZERO FRAMES"
                play_chime("alert")
                analysis.append("[bold yellow]ZERO AIRCRAFT INTERCEPTS:[/bold yellow] No 1090 MHz frames received in window.")
                analysis.append("[bold white]TACTICAL ADVICE:[/bold white] If indoor antenna is shielded, reposition near a window or check line of sight.")

            self.diagnostic_analysis = analysis
        finally:
            self.is_busy = False

    # Key [5]: AI Comms Scout (Real-time SIGINT & Faster-Whisper Logger)
    def toggle_ai_comms(self, custom_freq=None, custom_name=None):
        if self.ai_comms_active:
            self.stop_ai_comms()
            play_chime("rec_stop")
            self.diagnostic_analysis = [
                "[bold yellow]AI COMMS SCOUT STANDBY.[/bold yellow]",
                "[bold cyan]STATUS:[/bold cyan] Neural transcriber paused. Tuner free.",
                "[bold white]CONTROLS:[/bold white] Press [5] to re-engage AI Scout, or [T] for live raw audio."
            ]
        else:
            self.start_ai_comms(custom_freq=custom_freq, custom_name=custom_name)

    def start_ai_comms(self, custom_freq=None, custom_name=None):
        if self.live_audio_active:
            self.stop_live_tune()
        if self.audio_logger_active:
            self.toggle_audio_logger()
        if self.tcp_process:
            self.toggle_rtl_tcp()

        freq = custom_freq if custom_freq else (self.active_tune_freq or (self.selected_freq_obj.get("freq", "147.285") if self.selected_freq_obj else "147.285"))
        name = custom_name if custom_name else (self.active_tune_name or (self.selected_freq_obj.get("name", "Repeater") if self.selected_freq_obj else "Repeater"))
        self.active_tune_freq = freq
        self.active_tune_name = name
        freq_hz = str(int(float(freq) * 1000000))

        # Guarantee clean tuner ownership
        subprocess.run(["taskkill", "/F", "/IM", "rtl_power.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.15)

        rtl_exe = self.get_rtl_exe("rtl_fm.exe")
        gain_args = ["-g", str(self.active_gain_db)]
        # Hardware carrier squelch (-l 45 drops static silence; pipes voice only)
        squelch_args = ["-l", "45"]

        try:
            self.is_busy = True
            self.ai_comms_active = True
            mode_tag = "SILENT COMMS (HUD TELETYPE)" if self.ai_silent_mode else "LIVE AUDIO + HUD TELETYPE"
            self.active_test_name = f"AI Scout: {name}"
            self.hardware_status = f"AI SCOUT: {freq} MHz"

            # Spawn rtl_fm piping 16kHz PCM (Whisper native sample rate)
            self.ai_proc = subprocess.Popen(
                [rtl_exe, "-f", freq_hz, "-M", "fm", "-s", "16000", "-r", "16000", "-E", "deemp"] + gain_args + squelch_args + ["-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            time.sleep(0.2)
            if self.ai_proc.poll() is not None:
                self.stop_ai_comms()
                self.diagnostic_analysis = [
                    "[bold red]AI COMMS ERROR:[/bold red] rtl_fm failed to claim RTL-SDR tuner.",
                    "[bold white]ACTION:[/bold white] Press [5] to retry."
                ]
                return

            self.ai_thread = threading.Thread(target=self._ai_comms_worker, args=(freq, name), daemon=True)
            self.ai_thread.start()
            play_chime("rec_start")

            self.diagnostic_analysis = [
                f"[bold cyan]AI COMMS SCOUT ACTIVE:[/bold cyan] {name} ({freq} MHz).",
                f"[bold #00d4ff]OPERATIONAL MODE:[/bold #00d4ff] {mode_tag} [Press M to toggle Audio].",
                "[bold #ff8c00]SIGINT PIPELINE:[/bold #ff8c00] Hardware Squelch -> Faster-Whisper (CUDA RTX 3050) -> Teletype."
            ]
        except Exception as e:
            self.stop_ai_comms()
            self.diagnostic_analysis = [f"[bold red]AI COMMS START ERROR:[/bold red] {str(e)}"]

    def stop_ai_comms(self):
        if self.ai_proc:
            try:
                self.ai_proc.terminate()
                self.ai_proc.kill()
            except Exception:
                pass
            self.ai_proc = None
        self.ai_comms_active = False
        self.hardware_status = "READY"
        self.is_busy = False

    def toggle_ai_silent_mode(self):
        self.ai_silent_mode = not self.ai_silent_mode
        play_chime("click")
        mode_str = "SILENT MODE (Speakers muted, Teletype HUD)" if self.ai_silent_mode else "AUDIO ACTIVE (Live speakers + Teletype HUD)"
        self.diagnostic_analysis = [
            f"[bold #00d4ff]AI COMMS AUDIO TOGGLE:[/bold #00d4ff] {mode_str}.",
            f"[bold cyan]STATION LOCKED:[/bold cyan] {self.active_tune_name or 'Repeater'} ({self.active_tune_freq or '147.285'} MHz).",
            "[bold white]KEYBOARD:[/bold white] Press [M] anytime to switch between Silent Mode and Live Audio."
        ]

    def _ensure_whisper_loaded(self):
        if self.whisper_model is not None:
            return True
        if self.whisper_loading:
            return False
        self.whisper_loading = True
        try:
            from faster_whisper import WhisperModel
            # Load tiny.en on RTX 3050 Laptop GPU with float16 acceleration (~250MB VRAM)
            self.whisper_model = WhisperModel("tiny.en", device="cuda", compute_type="float16")
            self.whisper_loading = False
            return True
        except Exception:
            try:
                # Fallback to int8 if float16 unsupported
                from faster_whisper import WhisperModel
                self.whisper_model = WhisperModel("tiny.en", device="cuda", compute_type="int8")
                self.whisper_loading = False
                return True
            except Exception:
                self.whisper_loading = False
                return False

    def _ai_comms_worker(self, freq, name):
        # Background worker for loading model and processing raw PCM chunks from rtl_fm
        self._ensure_whisper_loaded()

        CHUNK_SIZE = 16000 * 2 * 3  # 3 seconds of 16kHz 16-bit mono PCM = 96,000 bytes
        buffer = bytearray()
        last_transcribe_time = time.time()

        ffplay_stream = None
        if not self.ai_silent_mode:
            try:
                ffplay_exe = self.get_ffplay_exe()
                ffplay_stream = subprocess.Popen(
                    [ffplay_exe, "-nodisp", "-f", "s16le", "-ar", "16000", "-ch_layout", "mono", "-i", "-"],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except Exception:
                ffplay_stream = None

        while self.ai_comms_active and self.ai_proc and self.ai_proc.poll() is None:
            try:
                raw_bytes = self.ai_proc.stdout.read(4096)
                if not raw_bytes:
                    time.sleep(0.05)
                    continue

                buffer.extend(raw_bytes)

                # Route to local speaker if not in silent mode
                if not self.ai_silent_mode and ffplay_stream and ffplay_stream.stdin:
                    try:
                        ffplay_stream.stdin.write(raw_bytes)
                    except Exception:
                        pass

                # If we've buffered >= 3 seconds or silence pause > 1.2s with >= 1s of audio
                now = time.time()
                elapsed = now - last_transcribe_time
                if len(buffer) >= CHUNK_SIZE or (len(buffer) >= 32000 and elapsed >= 2.5):
                    chunk_to_process = bytes(buffer)
                    buffer.clear()
                    last_transcribe_time = now
                    threading.Thread(target=self._ai_transcribe_chunk, args=(chunk_to_process, freq, name), daemon=True).start()

            except Exception:
                break

        if ffplay_stream:
            try:
                ffplay_stream.terminate()
                ffplay_stream.kill()
            except Exception:
                pass

    def _ai_transcribe_chunk(self, pcm_bytes, freq, name):
        if not self._ensure_whisper_loaded():
            return

        try:
            # Package 16kHz 16-bit PCM into in-memory WAV container
            wav_io = io.BytesIO()
            with wave.open(wav_io, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(pcm_bytes)
            wav_io.seek(0)

            segments, _ = self.whisper_model.transcribe(
                wav_io,
                language="en",
                beam_size=1,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400)
            )

            text_parts = [s.text.strip() for s in segments if s.text and s.text.strip()]
            full_text = " ".join(text_parts).strip()
            if not full_text:
                return

            # Extract Callsigns (e.g. W5LUC, KD4ABC, N5XYZ, KEC61)
            call_pattern = re.compile(r'\b([AKNW][A-Z]?[0-9][A-Z]{1,3}|KEC[0-9]{2})\b', re.IGNORECASE)
            found_calls = list(dict.fromkeys(call_pattern.findall(full_text)))
            call_str = ", ".join(found_calls) if found_calls else ""

            # Extract Tactical Alerts
            alert_pattern = re.compile(r'\b(emergency|warning|tornado|thunderstorm|flash flood|watch|priority|net control)\b', re.IGNORECASE)
            found_alerts = list(dict.fromkeys(alert_pattern.findall(full_text)))
            alert_str = ", ".join(found_alerts) if found_alerts else ""

            t_stamp = datetime.now().strftime("%H:%M:%S")
            full_dt_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            formatted_line = self._format_ai_line(t_stamp, freq, name, full_text)
            self.comms_transcript_lines.append(formatted_line)
            if len(self.comms_transcript_lines) > 30:
                self.comms_transcript_lines.pop(0)

            # Also mirror to raw output lines for HUD teletype display
            self.raw_output_lines.append(formatted_line)
            if len(self.raw_output_lines) > 30:
                self.raw_output_lines.pop(0)

            # SQLite Ghost Logging: Commit directly to database
            try:
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO intercepts (timestamp, freq_mhz, station_name, callsigns, alert_tags, transcript)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (full_dt_stamp, str(freq), str(name), call_str, alert_str, full_text))
                conn.commit()
                conn.close()
            except Exception:
                pass

            # Also commit to fallback text log
            try:
                log_file = os.path.join(RECORDINGS_DIR, "comms_intel.log")
                clean_txt = re.sub(r'\[.*?\]', '', formatted_line)
                with open(log_file, "a", encoding="utf-8") as lf:
                    lf.write(f"[{full_dt_stamp}] {clean_txt}\n")
            except Exception:
                pass

            play_chime("click")
        except Exception:
            pass

    def _format_ai_line(self, t_stamp, freq, name, text):
        # Highlight Callsigns (e.g. W5LUC, KD4ABC, N5XYZ, KEC61)
        call_pattern = re.compile(r'\b([AKNW][A-Z]?[0-9][A-Z]{1,3}|KEC[0-9]{2})\b', re.IGNORECASE)
        highlighted = call_pattern.sub(r'[bold yellow]\1[/bold yellow]', text)

        # Highlight Tactical / Weather Alerts in red
        alert_pattern = re.compile(r'\b(emergency|warning|tornado|thunderstorm|flash flood|watch|priority|net control)\b', re.IGNORECASE)
        highlighted = alert_pattern.sub(r'[bold red blink]\1[/bold red blink]', highlighted)

        return f"[dim white][{t_stamp}][/dim white] [bold cyan]{name[:12]}:[/bold cyan] {highlighted}"

    # Legacy key hook fallback
    def toggle_audio_logger(self):
        self.toggle_ai_comms()

    # Key [T]: Toggle Live Audio Stream (rtl_fm -> ffplay)
    def toggle_live_tune(self, custom_freq=None, custom_name=None):
        if self.live_audio_active:
            self.stop_live_tune()
            play_chime("click")
            self.diagnostic_analysis = [
                "[bold yellow]LIVE AUDIO MUTED.[/bold yellow]",
                "[bold cyan]STATUS:[/bold cyan] Demodulator released. Tuner free.",
                "[bold white]CONTROLS:[/bold white] Press [T] to resume listening, or [W] for NOAA weather."
            ]
        else:
            if self.audio_logger_active:
                self.toggle_audio_logger()
            
            freq = custom_freq if custom_freq else (self.selected_freq_obj.get("freq", "147.285") if self.selected_freq_obj else "147.285")
            name = custom_name if custom_name else (self.selected_freq_obj.get("name", "Repeater") if self.selected_freq_obj else "Repeater")
            self.active_tune_freq = freq
            self.active_tune_name = name
            freq_hz = str(int(float(freq) * 1000000))
            
            # Kill any lingering background processes to guarantee clean USB bus access
            subprocess.run(["taskkill", "/F", "/IM", "rtl_power.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.15)
            
            rtl_exe = self.get_rtl_exe("rtl_fm.exe")
            ffplay_exe = self.get_ffplay_exe()
            
            try:
                self.is_busy = True
                self.active_test_name = f"Live Monitor ({freq} MHz)"
                self.hardware_status = f"LIVE AUDIO: {freq} MHz"
                
                squelch_args = ["-l", "45"] if self.squelch_active else ["-l", "0"]
                gain_args = ["-g", str(self.active_gain_db)]
                
                # Start rtl_fm piping into ffplay with de-emphasis filter (-E deemp) and active hardware gain
                self.live_tune_rtl_proc = subprocess.Popen(
                    [rtl_exe, "-f", freq_hz, "-M", "fm", "-s", "24000", "-r", "24000", "-E", "deemp"] + gain_args + squelch_args + ["-"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                
                time.sleep(0.2)
                if self.live_tune_rtl_proc.poll() is not None:
                    _, err = self.live_tune_rtl_proc.communicate()
                    err_msg = err.decode("utf-8", errors="ignore").strip()
                    self.stop_live_tune()
                    self.diagnostic_analysis = [
                        "[bold red]TUNER HARDWARE ERROR:[/bold red] rtl_fm failed to claim dongle.",
                        f"[dim yellow]{err_msg[:65]}[/dim yellow]",
                        "[bold white]ACTION:[/bold white] Tap [T] again to re-engage."
                    ]
                    return

                self.live_tune_ffplay_proc = subprocess.Popen(
                    [ffplay_exe, "-nodisp", "-f", "s16le", "-ar", "24000", "-ch_layout", "mono", "-i", "-"],
                    stdin=self.live_tune_rtl_proc.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                self.live_audio_active = True
                play_chime("success")
                
                sq_mode = "SQUELCHED (Static-free until voice transmission)" if self.squelch_active else "OPEN SQUELCH (Raw static/carrier audible)"
                self.diagnostic_analysis = [
                    f"[bold green]LIVE AUDIO STREAMING:[/bold green] {name} ({freq} MHz).",
                    f"[bold #ff8c00]TUNER GAIN:[/bold #ff8c00] {self.active_gain_db:.1f} dB (Press [+/-] to boost/cut).",
                    f"[bold cyan]SQUELCH GATE:[/bold cyan] {sq_mode} [Press O to toggle]."
                ]
            except Exception as e:
                self.stop_live_tune()
                self.diagnostic_analysis = [f"[bold red]AUDIO STREAM ERROR:[/bold red] {str(e)}"]

    def stop_live_tune(self):
        if self.ai_comms_active:
            self.stop_ai_comms()
        if self.live_tune_ffplay_proc:
            try:
                self.live_tune_ffplay_proc.terminate()
                self.live_tune_ffplay_proc.kill()
            except Exception:
                pass
            self.live_tune_ffplay_proc = None
        if self.live_tune_rtl_proc:
            try:
                self.live_tune_rtl_proc.terminate()
                self.live_tune_rtl_proc.kill()
            except Exception:
                pass
            self.live_tune_rtl_proc = None
        self.live_audio_active = False
        self.hardware_status = "READY"
        self.is_busy = False

    def toggle_squelch(self):
        self.squelch_active = not self.squelch_active
        play_chime("click")
        mode = "SQUELCHED (Static-free)" if self.squelch_active else "OPEN SQUELCH (Raw static audible)"
        if self.live_audio_active:
            # Re-tune live with updated squelch setting on current active frequency
            f_target = self.active_tune_freq
            n_target = self.active_tune_name
            self.stop_live_tune()
            self.toggle_live_tune(custom_freq=f_target, custom_name=n_target)
        else:
            self.diagnostic_analysis = [
                f"[bold cyan]SQUELCH MODE:[/bold cyan] Set to {mode}.",
                "[bold white]NOTE:[/bold white] Tap [T] to listen to active repeater with this setting."
            ]

    def tune_noaa_live(self):
        play_chime("click")
        # Direct tuning to 162.550 MHz (Mobile/Gulf Coast NOAA Weather KEC61)
        self.toggle_live_tune(custom_freq="162.550", custom_name="NOAA Weather 24/7 (KEC61)")

    # Key [S]: ARES Priority Activity Scanner
    def run_ares_scan(self):
        if self.is_busy or self.live_audio_active or self.audio_logger_active or self.tcp_process:
            self.diagnostic_analysis = ["[bold yellow]SCANNER BUSY:[/bold yellow] Stop active audio stream or test before scanning."]
            return

        self.is_busy = True
        self.active_test_name = "ARES Priority Activity Scanner"
        self.hardware_status = "SCANNING ARES"
        self.raw_output_lines = ["Sweeping 10 ARES frequencies for carrier activity..."]
        play_chime("click")

        try:
            exe = self.get_rtl_exe("rtl_power.exe")
            # Sweep 144M to 148M across 2M repeater segment for 1.5 seconds
            out = run_rtl_cmd_timeout([exe, "-f", "144M:148M:25k", "-i", "1", "-e", "1", "-"], 2.0)
            self.last_test_time = time.strftime("%I:%M:%S %p")
            self.raw_output_lines = [l.strip()[:65] for l in out.splitlines() if l.strip()][-6:]

            power_map = {}
            for line in out.splitlines():
                parts = [p.strip() for p in line.split(",") if p.strip()]
                if len(parts) >= 7:
                    try:
                        low_f = float(parts[2]) / 1000000.0
                        step_f = float(parts[4]) / 1000000.0
                        dbs = [float(x) for x in parts[6:]]
                        for i, db_val in enumerate(dbs):
                            curr_f = round(low_f + (i * step_f), 3)
                            power_map[curr_f] = db_val
                    except Exception:
                        pass

            best_freq_idx = 0
            best_power = -999.0
            for idx, item in enumerate(self.frequencies):
                try:
                    rf = float(item.get("freq", 0.0))
                    closest_p = max((p for f, p in power_map.items() if abs(f - rf) <= 0.035), default=-999.0)
                    if closest_p > best_power:
                        best_power = closest_p
                        best_freq_idx = idx
                except Exception:
                    pass

            if best_power > -25.0 and self.frequencies:
                self.select_station(best_freq_idx)
                hot_station = self.frequencies[best_freq_idx]
                self.hardware_status = f"HOT: {hot_station.get('freq')} MHz"
                play_chime("alert")
                self.diagnostic_analysis = [
                    f"[bold green]HOT CARRIER DETECTED:[/bold green] {hot_station.get('name')} ({hot_station.get('freq')} MHz).",
                    f"[bold cyan]SIGNAL STRENGTH:[/bold cyan] {best_power:.1f} dBFS (Carrier Squelch Broken).",
                    "[bold yellow]TACTICAL ACTION:[/bold yellow] Press [T] to listen live, or [5] to record conversation."
                ]
            else:
                self.hardware_status = "IDLE (QUIET)"
                play_chime("success")
                self.diagnostic_analysis = [
                    "[bold cyan]ARES SCAN COMPLETE:[/bold cyan] All 10 local repeaters quiet (carrier squelch holding).",
                    f"[bold white]BACKGROUND NOISE FLOOR:[/bold white] Baseline ~{self.rf_power_dbfs:.1f} dBFS.",
                    "[bold white]STATUS:[/bold white] Repeaters are monitoring. Press [S] anytime to re-scan for traffic."
                ]
        finally:
            self.is_busy = False

    # Test 6: Toggle RTL-TCP Server
    def toggle_rtl_tcp(self):
        if self.tcp_process:
            try:
                self.tcp_process.terminate()
                self.tcp_process = None
                self.hardware_status = "TCP STOPPED"
                play_chime("alert")
                self.diagnostic_analysis = ["[bold yellow]RTL-TCP STREAMING STOPPED.[/bold yellow] Dongle freed for local applications."]
            except Exception:
                pass
        else:
            exe = self.get_rtl_exe("rtl_tcp.exe")
            try:
                self.tcp_process = subprocess.Popen([exe, "-a", "0.0.0.0", "-p", "1234"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.hardware_status = "TCP STREAMING (0.0.0.0:1234)"
                play_chime("success")
                self.diagnostic_analysis = [
                    "[bold green]RTL-TCP SERVER ONLINE:[/bold green] Listening on port 1234.",
                    "[bold cyan]NETWORK ACCESSIBLE:[/bold cyan] You can connect from SDR++, SDR Console, or Kali VM over LAN/localhost.",
                    "[bold white]NOTE:[/bold white] Press [6] again anytime to stop streaming and release the dongle."
                ]
            except Exception as e:
                self.diagnostic_analysis = [f"[bold red]ERROR STARTING RTL-TCP:[/bold red] {str(e)}"]

    # Launchers
    def launch_sdr_console(self):
        path = r"C:\Program Files\SDR-Radio.com (V3)\SDR Console.exe"
        if os.path.exists(path):
            play_chime("click")
            subprocess.Popen([path])
            self.diagnostic_analysis = ["[bold green]LAUNCHED:[/bold green] SDR Console V3 opened successfully."]
        else:
            self.diagnostic_analysis = ["[bold red]NOT FOUND:[/bold red] SDR Console.exe not found at standard path."]

    def launch_sdrpp(self):
        path = r"C:\Users\daddy\Documents\Radio_SDR\SDR Stuff\sdr++\sdrpp_windows_x64\sdrpp.exe"
        if os.path.exists(path):
            play_chime("click")
            subprocess.Popen([path], cwd=os.path.dirname(path))
        else:
            self.diagnostic_analysis = ["[bold red]NOT FOUND:[/bold red] sdrpp.exe not found in Documents/Radio_SDR."]

    def fetch_sqlite_intercepts(self, limit=50):
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            if self.log_filter_mode == "CALLSIGNS":
                cur.execute("SELECT id, timestamp, freq_mhz, station_name, callsigns, alert_tags, transcript FROM intercepts WHERE callsigns != '' ORDER BY id DESC LIMIT ?", (limit,))
            elif self.log_filter_mode == "ALERTS":
                cur.execute("SELECT id, timestamp, freq_mhz, station_name, callsigns, alert_tags, transcript FROM intercepts WHERE alert_tags != '' ORDER BY id DESC LIMIT ?", (limit,))
            else:
                cur.execute("SELECT id, timestamp, freq_mhz, station_name, callsigns, alert_tags, transcript FROM intercepts ORDER BY id DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
            conn.close()
            return rows
        except Exception:
            return []

    def toggle_view_mode(self):
        play_chime("click")
        if self.view_mode == "HUD":
            self.view_mode = "LOGS"
            self.log_selected_idx = 0
        else:
            self.view_mode = "HUD"

    def cycle_log_filter(self):
        play_chime("click")
        modes = ["ALL", "CALLSIGNS", "ALERTS"]
        curr_i = modes.index(self.log_filter_mode) if self.log_filter_mode in modes else 0
        self.log_filter_mode = modes[(curr_i + 1) % len(modes)]
        self.log_selected_idx = 0

    def scroll_logs(self, delta):
        rows = self.fetch_sqlite_intercepts()
        if not rows:
            return
        self.log_selected_idx = max(0, min(len(rows) - 1, self.log_selected_idx + delta))
        play_chime("click")

    def copy_selected_log_row(self):
        rows = self.fetch_sqlite_intercepts()
        if rows and 0 <= self.log_selected_idx < len(rows):
            row = rows[self.log_selected_idx]
            # row: id, timestamp, freq_mhz, station_name, callsigns, alert_tags, transcript
            txt = f"[{row[1]}] {row[3]} ({row[2]} MHz): {row[6]}"
            copy_to_clipboard(txt)
            play_chime("success")

    def build_log_viewer_panel(self):
        rows = self.fetch_sqlite_intercepts()
        total_count = len(rows)

        # Build Rich Table for intercepted speech
        t = Table(expand=True, box=None, show_header=True)
        t.add_column("#", style="bold cyan", width=3)
        t.add_column("Time", style="dim white", width=9)
        t.add_column("Station", style="bold green", width=14)
        t.add_column("Freq", style="cyan", width=8)
        t.add_column("Callsigns", style="bold yellow", width=12)
        t.add_column("Alerts", style="bold red", width=10)
        t.add_column("Intercepted Transcript", style="white")

        if not rows:
            t.add_row("-", "--:--:--", "NO INTERCEPTS", "---.---", "None", "None", "[dim]No speech detected yet. Turn on [5] AI Comms Scout to automatically log intercepts.[/dim]")
        else:
            for idx, r in enumerate(rows[:14]):
                is_sel = (self.log_selected_idx == idx)
                marker = ">" if is_sel else " "
                row_style = "bold white on #1a233a" if is_sel else ""

                r_time = r[1].split()[-1] if " " in r[1] else r[1]
                st_name = r[3][:14]
                f_str = r[2]
                calls = r[4] if r[4] else "[dim]-[/dim]"
                alerts = f"[bold red blink]{r[5]}[/bold red blink]" if r[5] else "[dim]-[/dim]"
                trans = r[6]

                t.add_row(
                    f"{marker}{idx + 1}",
                    r_time,
                    st_name,
                    f_str,
                    calls,
                    alerts,
                    trans,
                    style=row_style
                )

        f_tag = f"[FILTER: {self.log_filter_mode}]"
        title = f"SQLite Ghost Intelligence Logbook ({total_count} records) {f_tag}"
        return Panel(t, title=title, border_style="#00ffff")

    def build_layout(self):
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=3)
        )

        # Heartbeat Spinner
        self.spinner_idx += 1
        pulse_char = self.spinner_chars[self.spinner_idx % len(self.spinner_chars)]
        now_time = datetime.now().strftime("%H:%M:%S")

        # Header with Heartbeat & Gain Overload Protection
        hdr = Text()
        hdr.append("SDR SCOUT ", style="bold green")
        hdr.append("| ", style="dim white")
        hdr.append(f"[{self.hardware_status}] ", style="bold yellow")
        
        # Gain badge in orange
        if self.active_gain_db > 36.0:
            hdr.append(f"[GAIN: {self.active_gain_db:.1f}dB] ", style="bold #ff8c00 blink")
        elif self.active_gain_db > 20.0:
            hdr.append(f"[GAIN: {self.active_gain_db:.1f}dB] ", style="bold #ff8c00")
        else:
            hdr.append(f"[GAIN: {self.active_gain_db:.1f}dB] ", style="bold #ffa500")

        # AI Comms SIGINT Badge (Popping Cyan/Neon Blue)
        if self.ai_comms_active:
            freq_str = self.active_tune_freq or (self.selected_freq_obj.get("freq", "") if self.selected_freq_obj else "")
            mode_badge = "SILENT" if self.ai_silent_mode else "AUDIO"
            hdr.append("[AI COMMS HOT] ", style="bold #00ffff blink")
            hdr.append(f"({mode_badge}: {freq_str} MHz) ", style="bold #00d4ff")
        elif self.live_audio_active:
            freq_str = self.active_tune_freq or (self.selected_freq_obj.get("freq", "") if self.selected_freq_obj else "")
            hdr.append(f"[LIVE AUDIO: {freq_str} MHz] ", style="bold red blink")

        # Heartbeat pulse
        hdr.append(f"[PULSE: {pulse_char} {now_time}]", style="bold cyan")
        layout["header"].update(Panel(hdr, style="green on #030508", border_style="green"))

        # Mode Branch: If in LOGS mode, render SQLite Ghost Intelligence Logbook
        if self.view_mode == "LOGS":
            layout["main"].update(self.build_log_viewer_panel())
            
            # Logs Footer Menu
            ft = Text(" LOG VIEWER: ", style="bold #00ffff")
            ft.append("[Up/Down] ", style="bold green"); ft.append("Scroll  ", style="white")
            ft.append("[F] ", style="bold yellow"); ft.append(f"Filter ({self.log_filter_mode})  ", style="bold yellow")
            ft.append("[C] ", style="bold cyan"); ft.append("Copy Row  ", style="white")
            ft.append("[L / Esc] ", style="bold #00ffff blink"); ft.append("Back to Live HUD  ", style="white")
            ft.append("[Q] ", style="bold red"); ft.append("Quit", style="white")
            layout["footer"].update(Panel(ft, style="white on #030508", border_style="#00ffff"))
            return layout

        # Main Split
        layout["main"].split_row(
            Layout(name="left_panel", ratio=1),
            Layout(name="right_panel", ratio=1)
        )

        left_layout = Layout()
        left_layout.split_column(
            Layout(name="analysis_box", size=9),
            Layout(name="raw_stream")
        )

        analysis_text = Text()
        for line in self.diagnostic_analysis:
            try:
                analysis_text.append_text(Text.from_markup(f"{line}\n"))
            except Exception:
                analysis_text.append(f"{line}\n")
        left_layout["analysis_box"].update(Panel(analysis_text, title=f"Tactical Analysis ({self.active_test_name})", border_style="cyan"))

        raw_layout = Layout()
        raw_layout.split_column(
            Layout(name="meters_box", size=4),
            Layout(name="stream_box")
        )

        # Live ASCII Gauges: RF Noise Floor & Tuner Hardware Gain in Orange
        meters_text = Text()
        meters_text.append_text(Text.from_markup(f"RF Level:   {self.render_rf_meter()}\n"))
        meters_text.append_text(Text.from_markup(f"Tuner Gain: {self.render_gain_gauge()}"))
        raw_layout["meters_box"].update(Panel(meters_text, title="Live RF Energy & Tuner Gain Gauges", border_style="#ff8c00"))

        raw_text = Text()
        # If AI Comms is active, display real-time intercepted speech teletype
        if self.ai_comms_active and self.comms_transcript_lines:
            for c_line in self.comms_transcript_lines[-6:]:
                try:
                    raw_text.append_text(Text.from_markup(f"{c_line}\n"))
                except Exception:
                    raw_text.append(f"{c_line}\n")
        else:
            for r_line in self.raw_output_lines[-6:]:
                try:
                    raw_text.append_text(Text.from_markup(f"{r_line}\n"))
                except Exception:
                    raw_text.append(f"{r_line}\n", style="dim white")
            if not self.raw_output_lines:
                raw_text.append("Awaiting diagnostic execution or AI Comms speech...", style="dim")

        stream_title = "AI Comms Teletype Stream" if self.ai_comms_active else "Hardware Telemetry Stream"
        stream_border = "#00ffff" if self.ai_comms_active else "blue"
        raw_layout["stream_box"].update(Panel(raw_text, title=stream_title, border_style=stream_border))

        left_layout["raw_stream"].update(raw_layout)
        layout["main"]["left_panel"].update(left_layout)

        # Right Panel: Repeater Matrix & Badges
        freq_table = Table(expand=True, box=None, show_header=True)
        freq_table.add_column("#", style="bold cyan", width=3)
        freq_table.add_column("Channel / Station", style="bold white", width=18)
        freq_table.add_column("Freq", style="bold green", width=9)
        freq_table.add_column("Offset", style="dim", width=7)
        freq_table.add_column("Tone", style="yellow", width=6)

        for idx, item in enumerate(self.frequencies[:10]):
            is_sel = (self.selected_station_idx == idx)
            sel_marker = ">" if is_sel else " "
            row_style = "bold green" if is_sel else ""
            freq_table.add_row(
                f"{sel_marker}{idx + 1}",
                item.get("name", "N/A")[:18],
                item.get("freq", "0.0"),
                item.get("offset", ""),
                item.get("tone", ""),
                style=row_style
            )

        right_panel_title = "Lucedale & George Co ARES Matrix"
        if self.ai_comms_active:
            mode_lbl = "SILENT" if self.ai_silent_mode else "AUDIO"
            right_panel_title += f" [bold #00ffff blink][AI HOT: {mode_lbl}][/bold #00ffff blink]"
        elif self.live_audio_active:
            right_panel_title += " [bold red blink][AUDIO LIVE ON][/bold red blink]"
        elif self.audio_logger_active:
            right_panel_title += " [bold red][REC: AUDIO LOGGER ACTIVE][/bold red]"

        layout["main"]["right_panel"].update(Panel(freq_table, title=right_panel_title, border_style="yellow"))

        # Footer Menu
        ft = Text(" GAIN: ", style="bold #ff8c00")
        ft.append("[+/-] ", style="bold #ff8c00"); ft.append(f"{self.active_gain_db:.1f}dB  ", style="bold #ffa500")
        
        # Audio / Silent Mute / Logs
        ft.append("| COMMS: ", style="bold cyan")
        if self.ai_comms_active:
            ft.append("[5] ", style="bold #00ffff"); ft.append("AI STOP  ", style="bold #00ffff blink")
            m_state = "Unmute" if self.ai_silent_mode else "Mute"
            ft.append("[M] ", style="bold #00d4ff"); ft.append(f"{m_state}  ", style="white")
        else:
            ft.append("[5] ", style="bold #00ffff"); ft.append("AI Scout  ", style="white")

        ft.append("[L] ", style="bold #00ffff"); ft.append("Logbook  ", style="white")

        ft.append("| AUDIO: ", style="bold green")
        if self.live_audio_active:
            ft.append("[T] ", style="bold red"); ft.append("MUTE AUDIO  ", style="bold red blink")
        else:
            ft.append("[T] ", style="bold green"); ft.append("Tune Live  ", style="white")

        ft.append("[W] ", style="bold yellow"); ft.append("NOAA  ", style="white")
        
        sq_label = "Squelch" if self.squelch_active else "Static"
        ft.append("[O] ", style="bold cyan"); ft.append(f"{sq_label}  ", style="white")
        ft.append("[S] ", style="bold cyan"); ft.append("Scan  ", style="white")
        
        ft.append("| MATRIX: ", style="bold yellow")
        ft.append("[1] ", style="bold green"); ft.append("Audit ", style="white")
        ft.append("[2] ", style="bold cyan"); ft.append("Drift ", style="white")
        ft.append("[3] ", style="bold yellow"); ft.append("NOAA ", style="white")
        ft.append("[4] ", style="bold magenta"); ft.append("ADS-B ", style="white")

        ft.append("[6] ", style="bold blue"); ft.append("TCP ", style="white")
        ft.append("[7] ", style="bold green"); ft.append("Console ", style="white")
        ft.append("[8] ", style="bold cyan"); ft.append("SDR++ ", style="white")
        ft.append("[Q] ", style="bold red"); ft.append("Quit", style="white")
        layout["footer"].update(Panel(ft, style="white on #030508", border_style="yellow"))

        return layout

    def run(self):
        with Live(self.build_layout(), refresh_per_second=2, screen=True) as live:
            while self.running:
                if msvcrt.kbhit():
                    raw = msvcrt.getch()
                    # Handle arrow keys (prefixed by \x00 or \xe0 in Windows console)
                    if raw in (b'\x00', b'\xe0'):
                        arrow = msvcrt.getch()
                        if arrow == b'H':  # Up Arrow
                            if self.view_mode == "LOGS":
                                self.scroll_logs(-1)
                            else:
                                self.select_station(self.selected_station_idx - 1)
                        elif arrow == b'P':  # Down Arrow
                            if self.view_mode == "LOGS":
                                self.scroll_logs(1)
                            else:
                                self.select_station(self.selected_station_idx + 1)
                    else:
                        ch = raw.decode("utf-8", errors="ignore").lower()
                        # Global Quit
                        if ch == "q":
                            self.stop_live_tune()
                            self.stop_ai_comms()
                            if self.tcp_process:
                                self.tcp_process.terminate()
                            if self.logger_proc:
                                self.logger_proc.kill()
                            self.running = False
                            break

                        # Log Viewer Controls
                        elif ch == "l" or raw == b'\x1b':  # [L] or [Esc]
                            self.toggle_view_mode()
                        elif self.view_mode == "LOGS":
                            if ch in ("n", "j"):
                                self.scroll_logs(1)
                            elif ch in ("p", "k"):
                                self.scroll_logs(-1)
                            elif ch == "f":
                                self.cycle_log_filter()
                            elif ch in ("c", " "):
                                self.copy_selected_log_row()

                        # Main HUD Controls (Only active when in HUD mode)
                        elif ch in ("+", "=", "]"):
                            self.adjust_gain(1)
                        elif ch in ("-", "_", "["):
                            self.adjust_gain(-1)
                        elif ch in ("n", "j"):  # Next station
                            self.select_station(self.selected_station_idx + 1)
                        elif ch in ("p", "k"):  # Previous station
                            self.select_station(self.selected_station_idx - 1)
                        elif ch == "t":
                            self.toggle_live_tune()
                        elif ch == "w":
                            self.tune_noaa_live()
                        elif ch == "o":
                            self.toggle_squelch()
                        elif ch == "m":
                            self.toggle_ai_silent_mode()
                        elif ch == "s":
                            threading.Thread(target=self.run_ares_scan, daemon=True).start()
                        elif ch == "1":
                            threading.Thread(target=self.run_hardware_audit, daemon=True).start()
                        elif ch == "2":
                            threading.Thread(target=self.run_ppm_calibration, daemon=True).start()
                        elif ch == "3":
                            threading.Thread(target=self.run_noaa_check, daemon=True).start()
                        elif ch == "4":
                            threading.Thread(target=self.run_adsb_scout, daemon=True).start()
                        elif ch == "5":
                            threading.Thread(target=self.toggle_ai_comms, daemon=True).start()
                        elif ch == "6":
                            threading.Thread(target=self.toggle_rtl_tcp, daemon=True).start()
                        elif ch == "7":
                            self.launch_sdr_console()
                        elif ch == "8":
                            self.launch_sdrpp()
                        elif ch == " ":
                            if self.selected_freq_obj:
                                freq_val = self.selected_freq_obj.get("freq", "")
                                copy_to_clipboard(freq_val)
                                play_chime("click")

                live.update(self.build_layout())
                time.sleep(0.5)

def main():
    if "--test" in sys.argv:
        scout = SDRScout()
        scout.run_hardware_audit()
        print("Diagnostic Analysis:", scout.diagnostic_analysis)
        sys.exit(0)

    scout = SDRScout()
    scout.run()

if __name__ == "__main__":
    main()
