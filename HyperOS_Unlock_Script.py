import os
import sys
import time
import socket
import struct
import platform
import json
import hashlib
import random
import linecache
import threading
import queue
from datetime import datetime, timezone, timedelta
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog
from tkinter import ttk

# ==========================================
# CONFIGURATION & GLOBAL STATE
# ==========================================
VERSION = "1.0"

NTP_SERVERS = [
    "pool.ntp.org",
    "time.windows.com",
    "time.apple.com",
    "cn.pool.ntp.org",
    "ntp.aliyun.com"
]

# Shared thread-safe state
state = {
    "running": False,
    "beijing_time_str": "--:--:--.---",
    "countdown_str": "--:--:--.---",
    "is_synced": False,
    "offset": 0.0,
    "device_id": "",
    "log_queue": queue.Queue(),
    "active_token": "",
    "active_line": 1,
    "target_mode": "beijing_midnight",  # "beijing_midnight" or "custom"
    "custom_target_time": "00:00:00",    # HH:MM:SS Beijing time
    "calibrating": False,
    "calibrated_rtt": 0.0,
    "calibrated_timeshift": 1400.0,
    "ntp_server_details": {}
}

# Thread lock for safety
state_lock = threading.Lock()

# ==========================================
# EXTRAS: LOGGING & SOUND SYSTEMS
# ==========================================
def write_to_log_file(level, message):
    """Saves timestamps and logs to a local file for future troubleshooting."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    log_line = f"[{timestamp}] [{level.upper()}] {message}\n"
    try:
        with open("hyperos_unlock.log", "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception:
        pass

def log_message(tag, text):
    """Pushes log text to queue and logs to file."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    full_text = f"[{timestamp}] {text}\n"
    state["log_queue"].put((tag, full_text))
    write_to_log_file(tag, text)

def log_info(msg): log_message("info", msg)
def log_success(msg): log_message("success", msg)
def log_warning(msg): log_message("warning", msg)
def log_error(msg): log_message("error", msg)
def log_request(msg): log_message("request", msg)
def log_response(msg): log_message("response", msg)

def play_sound(sound_type):
    """Triggers high-quality, zero-dependency Windows system sounds."""
    if platform.system() == "Windows":
        def _beep():
            try:
                import winsound
                if sound_type == "success":
                    winsound.Beep(880, 150)
                    winsound.Beep(1100, 150)
                    winsound.Beep(1320, 300)
                elif sound_type == "error":
                    winsound.Beep(220, 500)
                elif sound_type == "warning":
                    winsound.Beep(440, 200)
                    winsound.Beep(440, 200)
                elif sound_type == "trigger":
                    winsound.Beep(1500, 400)
            except Exception:
                pass
        threading.Thread(target=_beep, daemon=True).start()

# ==========================================
# DATA STORAGE LAYER (Backward Compatible)
# ==========================================
class TokenStorage:
    """Manages line-based storage of token.txt and timeshift.txt with full compatibility."""
    
    @staticmethod
    def load_all():
        """Reads files and aligns them into structural records."""
        tokens = []
        shifts = []
        
        # Load token.txt
        if os.path.exists("token.txt"):
            try:
                with open("token.txt", "r", encoding="utf-8") as f:
                    tokens = [line.strip() for line in f]
            except Exception as e:
                log_error(f"Failed to read token.txt: {e}")
                
        # Load timeshift.txt
        if os.path.exists("timeshift.txt"):
            try:
                with open("timeshift.txt", "r", encoding="utf-8") as f:
                    for line in f:
                        line_str = line.strip()
                        if line_str:
                            try:
                                shifts.append(float(line_str))
                            except ValueError:
                                shifts.append(0.0)
                        else:
                            shifts.append(0.0)
            except Exception as e:
                log_error(f"Failed to read timeshift.txt: {e}")
                
        # Align lengths
        max_len = max(len(tokens), len(shifts))
        while len(tokens) < max_len:
            tokens.append("")
        while len(shifts) < max_len:
            shifts.append(0.0)
            
        records = []
        for i in range(max_len):
            records.append({
                "line": i + 1,
                "token": tokens[i],
                "timeshift": shifts[i],
                "status": "Not Verified"
            })
        return records

    @staticmethod
    def save_all(records):
        """Saves records back to files preserving accurate lines."""
        tokens = []
        shifts = []
        
        # Sort by line to preserve index ordering
        sorted_records = sorted(records, key=lambda x: x["line"])
        for r in sorted_records:
            tokens.append(r["token"])
            shifts.append(str(int(r["timeshift"])))
            
        try:
            with open("token.txt", "w", encoding="utf-8") as f:
                f.write("\n".join(tokens) + "\n")
            with open("timeshift.txt", "w", encoding="utf-8") as f:
                f.write("\n".join(shifts) + "\n")
            # Clear linecache to ensure accurate loads
            linecache.clearcache()
            return True
        except Exception as e:
            log_error(f"Failed to save tokens: {e}")
            return False

def generate_device_id():
    """Generates a stable, high-entropy unique device ID."""
    random_data = f"{random.random()}-{time.time()}"
    return hashlib.sha1(random_data.encode('utf-8')).hexdigest().upper()

state["device_id"] = generate_device_id()

# ==========================================
# NTP TIME SYNCHRONIZATION CLIENT
# ==========================================
def query_ntp_offset(server):
    """
    Queries an NTP server using a raw UDP socket.
    Computes precise clock offset and round-trip time.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.5)
        
        # 48-byte NTP packet. Client mode = 3, Version = 3 -> 0x1B
        packet = bytearray(48)
        packet[0] = 0x1B
        
        t0 = time.time()
        sock.sendto(packet, (server, 123))
        data, addr = sock.recvfrom(1024)
        t3 = time.time()
        sock.close()
        
        if len(data) < 48:
            return None, None
            
        NTP_EPOCH_OFFSET = 2208988800
        
        # Receive Timestamp (T1) starts at byte 32
        rx_sec, rx_frac = struct.unpack("!II", data[32:40])
        T1 = (rx_sec - NTP_EPOCH_OFFSET) + float(rx_frac) / 2**32
        
        # Transmit Timestamp (T2) starts at byte 40
        tx_sec, tx_frac = struct.unpack("!II", data[40:48])
        T2 = (tx_sec - NTP_EPOCH_OFFSET) + float(tx_frac) / 2**32
        
        # Precise offset and RTT calculations
        offset = ((T1 - t0) + (T2 - t3)) / 2.0
        rtt = (t3 - t0) - (T2 - T1)
        
        return offset, rtt
    except Exception:
        return None, None

def synchronize_time(force_log=True):
    """Queries all NTP servers and calculates a highly stable median offset."""
    offsets = []
    server_details = {}
    
    if force_log:
        log_info("Establishing connection to NTP servers...")
        
    for server in NTP_SERVERS:
        offset, rtt = query_ntp_offset(server)
        if offset is not None:
            offsets.append(offset)
            server_details[server] = {"rtt": rtt * 1000, "offset": offset * 1000}
            if force_log:
                log_info(f"NTP [{server}]: Latency {rtt*1000:.1f}ms | Offset {offset*1000:+.1f}ms")
            if len(offsets) >= 3:
                break
                
    with state_lock:
        state["ntp_server_details"] = server_details
        
    if not offsets:
        if force_log:
            log_error("Failed to obtain response from any NTP server. Check internet connection.")
        return False
        
    median_offset = sorted(offsets)[len(offsets) // 2]
    
    with state_lock:
        state["offset"] = median_offset
        state["is_synced"] = True
        
    if force_log:
        log_success(f"Time synchronized successfully. Local clock adjustment: {median_offset * 1000:+.2f} ms.")
        play_sound("success")
    return True

# Background daemon for continuous NTP calibration
def continuous_ntp_sync_daemon():
    """Periodically syncs in the background to prevent local clock drift."""
    while True:
        # Sync every 10 minutes
        for _ in range(600):
            time.sleep(1)
        if not state["running"]:
            synchronize_time(force_log=False)

threading.Thread(target=continuous_ntp_sync_daemon, daemon=True).start()

# ==========================================
# HIGH-PRECISION HTTPS CLIENT
# ==========================================
class HTTPSession:
    """Manages persistent HTTPS connections with connection re-use and RTT tracking."""
    def __init__(self, host="sgp-api.buy.mi.com"):
        self.host = host
        self.conn = None
        
    def get_connection(self):
        import http.client
        if self.conn is None:
            self.conn = http.client.HTTPSConnection(self.host, timeout=8)
        return self.conn
        
    def close(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
            
    def make_request(self, method, path, headers, body=None):
        import http.client
        for attempt in range(3):
            try:
                connection = self.get_connection()
                t0 = time.time()
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                data = response.read()
                rtt = (time.time() - t0) * 1000.0
                return response.status, data, rtt
            except (http.client.HTTPException, socket.error, Exception):
                self.close()
                time.sleep(0.1)
        return None, None, 0.0

# ==========================================
# TIME CALCULATIONS & TIMEZONES
# ==========================================
def get_synchronized_beijing_time():
    """Gets current Pekin Time (UTC+8) adjusting local time by NTP offset."""
    local_now = time.time()
    synced_epoch = local_now + state["offset"]
    beijing_tz = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(synced_epoch, beijing_tz)

def calculate_target_timestamps():
    """Computes target Beijing trigger time and target Unix epoch."""
    now_bj = get_synchronized_beijing_time()
    
    with state_lock:
        mode = state["target_mode"]
        custom_time_str = state["custom_target_time"]
        offset = state["offset"]
        
    if mode == "beijing_midnight":
        tomorrow_bj = now_bj + timedelta(days=1)
        target_bj = tomorrow_bj.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        # Custom trigger time parser (HH:MM:SS)
        try:
            parts = custom_time_str.split(":")
            h = int(parts[0])
            m = int(parts[1])
            s = int(parts[2]) if len(parts) > 2 else 0
        except Exception:
            h, m, s = 0, 0, 0
            
        target_bj = now_bj.replace(hour=h, minute=m, second=s, microsecond=0)
        if target_bj <= now_bj:
            # Schedule for tomorrow if custom time has already passed today
            target_bj += timedelta(days=1)
            
    target_epoch = target_bj.timestamp() - offset
    return target_bj, target_epoch

# ==========================================
# BACKGROUND WORKER FOR VERIFICATION & LATENCY
# ==========================================
def verify_token_bg(token, record_ref=None, app_ref=None):
    """Queries the API to verify if the token is active."""
    session = HTTPSession()
    is_valid = False
    
    try:
        url = "/bbs/api/global/user/bl-switch/state"
        headers = {
            "Cookie": f"new_bbs_serviceToken={token};versionCode=500411;versionName=5.4.11;deviceId={state['device_id']};",
            "User-Agent": "okhttp/4.12.0",
            "Connection": "keep-alive"
        }
        log_info("Verifying account permissions...")
        status, data, rtt = session.make_request('GET', url, headers=headers)
        
        if status is None:
            log_error("Verification Failed: Network communication error.")
            play_sound("error")
        else:
            response_data = json.loads(data.decode('utf-8'))
            code = response_data.get("code")
            
            if code == 100004:
                log_error("AUTH ERROR! Token has expired. Update credentials immediately.")
                play_sound("error")
                if record_ref: record_ref["status"] = "Expired"
            elif code == 0 or code == 200:
                body = response_data.get("data", {})
                is_pass = body.get("is_pass")
                button_state = body.get("button_state")
                deadline = body.get("deadline_format", "")
                
                is_valid = True
                if record_ref: record_ref["status"] = "Active"
                
                if is_pass == 4:
                    if button_state == 1:
                        log_success("Account Status: ELIGIBLE! Unlock request can be sent right now.")
                        play_sound("success")
                    elif button_state == 2:
                        log_warning(f"Account Status: BLOCKED from sending requests until {deadline} (Month/Day).")
                        play_sound("warning")
                    elif button_state == 3:
                        log_warning("Account Status: Account created less than 30 days ago.")
                        play_sound("warning")
                elif is_pass == 1:
                    log_success(f"CONGRATULATIONS! Request approved! Bootloader unlock available until {deadline}.")
                    play_sound("success")
                else:
                    log_warning(f"Account loaded. Code: {code} (Unknown status: {is_pass}).")
            else:
                log_warning(f"Unexpected response code: {code}. Raw body: {response_data}")
                play_sound("warning")
                if record_ref: record_ref["status"] = "Unknown"
    except Exception as e:
        log_error(f"Error during validation check: {e}")
        play_sound("error")
        if record_ref: record_ref["status"] = "Error"
        
    session.close()
    
    with state_lock:
        state["running"] = False
        
    # Safe GUI callback refresh
    if app_ref:
        app_ref.root.after(0, app_ref.refresh_token_table)

def run_latency_calibration_bg(app_ref=None):
    """Measures precise latency to Xiaomi endpoint and sets optimal timeshift."""
    with state_lock:
        state["calibrating"] = True
        
    log_info("Starting Network Latency Auto-Calibration...")
    log_info("Probing 'sgp-api.buy.mi.com' to calculate precise flight-time statistics...")
    
    session = HTTPSession()
    latencies = []
    
    for i in range(5):
        t0 = time.time()
        try:
            status, data, rtt = session.make_request('GET', "/bbs/api/global/user/bl-switch/state", {
                "User-Agent": "okhttp/4.12.0",
                "Connection": "keep-alive"
            })
            if status is not None:
                latencies.append(rtt)
                log_info(f"  Probe #{i+1}: RTT = {rtt:.1f} ms")
            else:
                log_warning(f"  Probe #{i+1} failed: No connection.")
        except Exception as e:
            log_warning(f"  Probe #{i+1} failed: {e}")
        time.sleep(0.2)
        
    session.close()
    
    with state_lock:
        state["calibrating"] = False
        
    if not latencies:
        log_error("Auto-calibration failed. Please check network connection.")
        play_sound("error")
        return
        
    avg_rtt = sum(latencies) / len(latencies)
    min_rtt = min(latencies)
    max_rtt = max(latencies)
    jitter = max_rtt - min_rtt
    
    # Calculate recommended timeshift (800ms handshake warmup + median RTT flight duration)
    recommended_shift = avg_rtt + 800.0
    
    with state_lock:
        state["calibrated_rtt"] = avg_rtt
        state["calibrated_timeshift"] = recommended_shift
        
    log_success(f"Calibration Completed! Min: {min_rtt:.1f}ms | Max: {max_rtt:.1f}ms | Avg: {avg_rtt:.1f}ms | Jitter: {jitter:.1f}ms")
    log_success(f"Recommended Timeshift (warmup + travel): {recommended_shift:.0f} ms")
    play_sound("success")
    
    if app_ref:
        app_ref.root.after(0, lambda: app_ref.timeshift_var.set(str(int(recommended_shift))))

# ==========================================
# HIGH-PRECISION UNLOCK SCHEDULER & ATTACK
# ==========================================
def run_unlock_automation_bg(token, timeshift):
    """High-precision automation loop executing on background thread."""
    global state
    
    # 1. Clock NTP sync
    if not state["is_synced"]:
        if not synchronize_time():
            log_error("NTP Synchronization failed. Automation aborted.")
            with state_lock:
                state["running"] = False
            return
            
    # 2. Check token status
    session = HTTPSession()
    verify_token_bg(token)
    
    with state_lock:
        still_running = state["running"]
    if not still_running:
        session.close()
        return
        
    # 3. Target schedule calculation
    target_bj, target_timestamp = calculate_target_timestamps()
    timeshift_sec = timeshift / 1000.0
    target_trigger = target_timestamp - timeshift_sec
    
    log_info(f"Target Timeshift Offset: {timeshift:.2f} ms")
    log_info(f"Scheduled Dispatch Target (Beijing Time): {target_bj.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
    log_info(f"High-Precision Wait active. Standby for optimal window...")
    
    # Enable multimedia timer on Windows OS for microsecond sleeping
    if platform.system() == "Windows":
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass
            
    try:
        # 4. Wait loop
        while True:
            with state_lock:
                running = state["running"]
            if not running:
                break
                
            now_local = time.time()
            time_diff = target_trigger - now_local
            
            # Format UI live clocks
            now_bj = get_synchronized_beijing_time()
            with state_lock:
                state["beijing_time_str"] = now_bj.strftime('%H:%M:%S.%f')[:-3]
                
                if time_diff > 0:
                    td_ms = int(time_diff * 1000)
                    hours = td_ms // 3600000
                    minutes = (td_ms % 3600000) // 60000
                    seconds = (td_ms % 60000) // 1000
                    ms = td_ms % 1000
                    state["countdown_str"] = f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms:03d}"
                else:
                    state["countdown_str"] = "00:00:00.000"
                    
            # Precise hybrid sleep-spin
            if time_diff > 0.015:
                # Sleep safely to use 0% CPU
                time.sleep(time_diff - 0.010)
            elif time_diff <= 0:
                # Deadline met!
                log_success(f"TRIGGER REACHED! Beijing: {now_bj.strftime('%H:%M:%S.%f')[:-3]}. Spawning rapid HTTP payload...")
                play_sound("trigger")
                break
            else:
                # Spin-lock under 10ms for extreme millisecond resolution
                pass
                
        # 5. Rapid dispatch phase
        if state["running"]:
            url = "/bbs/api/global/apply/bl-auth"
            headers = {
                "Cookie": f"new_bbs_serviceToken={token};versionCode=500411;versionName=5.4.11;deviceId={state['device_id']};",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "okhttp/4.12.0",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive"
            }
            body_data = json.dumps({"is_retry": True}).encode('utf-8')
            
            log_info("Firing high-frequency keep-alive POST pipeline...")
            req_count = 0
            
            while True:
                with state_lock:
                    still_running = state["running"]
                if not still_running:
                    break
                    
                req_count += 1
                send_time = get_synchronized_beijing_time()
                log_request(f"#{req_count} POST bl-auth dispatched at {send_time.strftime('%H:%M:%S.%f')[:-3]}")
                
                status, data, rtt = session.make_request('POST', url, headers=headers, body=body_data)
                
                if status is None:
                    log_error(f"#{req_count} Connection error during rapid-fire. Retrying...")
                    continue
                    
                recv_time = get_synchronized_beijing_time()
                log_response(f"#{req_count} Response in {rtt:.1f}ms at {recv_time.strftime('%H:%M:%S.%f')[:-3]} | HTTP Status: {status}")
                
                try:
                    json_response = json.loads(data.decode('utf-8'))
                    code = json_response.get("code")
                    data_body = json_response.get("data", {})
                    
                    if code == 0:
                        apply_result = data_body.get("apply_result")
                        if apply_result == 1:
                            log_success("CONGRATULATIONS! UNLOCK PERMISSION SUCCESSFULLY GRANTED!")
                            play_sound("success")
                            verify_token_bg(token)
                            break
                        elif apply_result == 3:
                            deadline = data_body.get("deadline_format", "Unspecified")
                            log_error(f"Daily quota limit reached. Xiaomi reset: {deadline}.")
                            play_sound("error")
                            break
                        elif apply_result == 4:
                            deadline = data_body.get("deadline_format", "Unspecified")
                            log_error(f"Account temporarily restricted/blocked until {deadline}.")
                            play_sound("error")
                            break
                        else:
                            log_warning(f"Unexpected application result: {apply_result}. Body: {json_response}")
                    elif code == 100001:
                        log_error(f"Server rejection (100001): {json_response}")
                    elif code == 100003:
                        log_success("Possible approval detected (100003)! Re-checking credentials status...")
                        play_sound("success")
                        verify_token_bg(token)
                        break
                    else:
                        log_warning(f"Server response code {code}: {json_response}")
                except Exception as e:
                    log_error(f"Failed to decode response: {e}")
                    
    finally:
        # Clean up OS timer resolution to preserve system power
        if platform.system() == "Windows":
            try:
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass
                
        session.close()
        with state_lock:
            state["running"] = False
        log_info("Automation script has stopped.")

# ==========================================
# MODERN HOVER BUTTON CUSTOM COMPONENT
# ==========================================
class HoverButton(tk.Button):
    """Flat button with animated color hovers."""
    def __init__(self, master, active_bg, normal_bg, active_fg="white", normal_fg="white", *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        self.active_bg = active_bg
        self.normal_bg = normal_bg
        self.active_fg = active_fg
        self.normal_fg = normal_fg
        
        self.config(
            bg=self.normal_bg,
            fg=self.normal_fg,
            activebackground=self.active_bg,
            activeforeground=self.active_fg,
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=4,
            cursor="hand2"
        )
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        
    def on_enter(self, event):
        if self["state"] != "disabled":
            self.config(bg=self.active_bg, fg=self.active_fg)
        
    def on_leave(self, event):
        if self["state"] != "disabled":
            self.config(bg=self.normal_bg, fg=self.normal_fg)

# ==========================================
# CORE GUI APPLICATION INTERFACE
# ==========================================
class App:
    def __init__(self, root):
        self.root = root
        self.root.title(f"HyperOS Unlock Tool")
        self.root.geometry("1080x760")
        self.root.minsize(980, 680)
        self.root.configure(bg="#0F0F11")
        
        # Set matching window icon
        try:
            if getattr(sys, 'frozen', False):
                icon_path = os.path.join(sys._MEIPASS, "icon.ico") if hasattr(sys, "_MEIPASS") else "icon.ico"
            else:
                icon_path = "icon.ico"
            if os.path.exists(icon_path):
                self.root.iconbitmap(icon_path)
        except Exception:
            pass
        
        # Core data source
        self.records = TokenStorage.load_all()
        
        # State variables
        self.active_line_var = tk.StringVar(value="1")
        self.timeshift_var = tk.StringVar(value="0")
        self.target_mode_var = tk.StringVar(value="beijing_midnight")
        self.custom_time_var = tk.StringVar(value="00:00:00")
        
        self.setup_styles()
        self.build_ui()
        
        # Initial status loading
        if self.records:
            self.select_account_line(1)
        else:
            self.active_token_lbl.config(text="No tokens loaded in token.txt")
            
        # Start NTP synchronization in background at boot
        t = threading.Thread(target=synchronize_time, args=(True,), daemon=True)
        t.start()
        
        # Launch main clock polling thread loop
        self.update_loop()

    def setup_styles(self):
        """Palette mapping for elegant, state-of-the-art dark interfaces."""
        self.bg_dark = "#0F0F11"
        self.bg_card = "#18181C"
        self.bg_entry = "#26262B"
        self.fg_white = "#E4E4E7"
        self.fg_muted = "#71717A"
        
        self.accent_blue = "#2979FF"
        self.accent_blue_hover = "#40C4FF"
        self.accent_green = "#10B981"
        self.accent_red = "#EF4444"
        self.accent_orange = "#F59E0B"
        self.accent_purple = "#8B5CF6"
        
        # Configure TTK styles
        style = ttk.Style()
        style.theme_use("clam")
        
        # Flat custom Treeview Styling
        style.configure("Treeview", 
                        background=self.bg_card, 
                        foreground=self.fg_white, 
                        rowheight=26, 
                        fieldbackground=self.bg_card,
                        font=("Segoe UI", 9),
                        borderwidth=0)
        style.map('Treeview', 
                  background=[('selected', self.accent_blue)], 
                  foreground=[('selected', 'white')])
        
        style.configure("Treeview.Heading", 
                        background="#242429", 
                        foreground=self.fg_white, 
                        font=("Segoe UI", 9, "bold"),
                        borderwidth=0)
        
        # Custom styles for tabs and scrollbars
        style.configure("Vertical.TScrollbar", gripcount=0, background=self.bg_entry, bordercolor=self.bg_dark, troughcolor=self.bg_dark, arrowsize=10)

    def build_ui(self):
        """Creates the two-column operation center grid layout."""
        
        # HEADER CARD
        header_frame = tk.Frame(self.root, bg=self.bg_dark, pady=10)
        header_frame.pack(fill="x", padx=20)
        
        title_lbl = tk.Label(header_frame, text="HYPEROS UNLOCK AUTOMATION", font=("Segoe UI", 16, "bold"), fg=self.accent_blue, bg=self.bg_dark)
        title_lbl.pack(side="left", anchor="w")
        
        self.lbl_status_badge = tk.Label(header_frame, text="READY", font=("Segoe UI", 9, "bold"), fg="white", bg=self.accent_green, padx=8, pady=2)
        self.lbl_status_badge.pack(side="left", padx=15)
        
        ver_lbl = tk.Label(header_frame, text=f"Version: {VERSION} | Zero-Dependency", font=("Segoe UI", 9), fg=self.fg_muted, bg=self.bg_dark)
        ver_lbl.pack(side="right", anchor="e")
        
        # MAIN DUAL CONTAINER
        body_container = tk.Frame(self.root, bg=self.bg_dark)
        body_container.pack(fill="both", expand=True, padx=20)
        
        # ==========================================
        # LEFT COLUMN (Operations, Timers, Settings)
        # ==========================================
        left_col = tk.Frame(body_container, bg=self.bg_dark, width=440)
        left_col.pack(side="left", fill="both", padx=(0, 10))
        left_col.pack_propagate(False)
        
        # CARD 1: Sync clocks & NTP
        clock_card = tk.Frame(left_col, bg=self.bg_card, bd=1, highlightbackground="#2C2C35", highlightcolor="#2C2C35", highlightthickness=1)
        clock_card.pack(fill="x", pady=(0, 10))
        
        tk.Label(clock_card, text="REAL-TIME TIME SYNC (BEIJING UTC+8)", font=("Segoe UI", 9, "bold"), fg=self.accent_blue, bg=self.bg_card).pack(anchor="w", padx=15, pady=(12, 5))
        
        time_row = tk.Frame(clock_card, bg=self.bg_card)
        time_row.pack(fill="x", padx=15, pady=5)
        
        tk.Label(time_row, text="Synchronized Clock:", font=("Segoe UI", 9), fg=self.fg_white, bg=self.bg_card).pack(side="left")
        self.lbl_beijing_clock = tk.Label(time_row, text="--:--:--.---", font=("Consolas", 14, "bold"), fg=self.accent_green, bg=self.bg_card)
        self.lbl_beijing_clock.pack(side="right")
        
        countdown_row = tk.Frame(clock_card, bg=self.bg_card)
        countdown_row.pack(fill="x", padx=15, pady=5)
        
        tk.Label(countdown_row, text="Countdown Timer:", font=("Segoe UI", 9), fg=self.fg_white, bg=self.bg_card).pack(side="left")
        self.lbl_countdown = tk.Label(countdown_row, text="--:--:--.---", font=("Consolas", 15, "bold"), fg=self.accent_orange, bg=self.bg_card)
        self.lbl_countdown.pack(side="right")
        
        status_row = tk.Frame(clock_card, bg=self.bg_card)
        status_row.pack(fill="x", padx=15, pady=(5, 12))
        
        self.lbl_sync_indicator = tk.Label(status_row, text="CLOCK DRIFT NOT CALCULATED", font=("Segoe UI", 9, "bold"), fg=self.accent_red, bg=self.bg_card)
        self.lbl_sync_indicator.pack(side="left")
        
        self.btn_manual_sync = HoverButton(status_row, self.accent_blue_hover, self.bg_entry, text="Sync NTP Now", font=("Segoe UI", 8, "bold"), command=self.manual_ntp_sync)
        self.btn_manual_sync.pack(side="right")
        
        # CARD 2: Schedulers & Timeshift Callibrator
        ctrl_card = tk.Frame(left_col, bg=self.bg_card, bd=1, highlightbackground="#2C2C35", highlightcolor="#2C2C35", highlightthickness=1)
        ctrl_card.pack(fill="x", pady=10)
        
        tk.Label(ctrl_card, text="AUTOMATION TARGET & LATENCY CALIBRATION", font=("Segoe UI", 9, "bold"), fg=self.accent_blue, bg=self.bg_card).pack(anchor="w", padx=15, pady=(12, 10))
        
        # Target mode radios
        mode_frame = tk.Frame(ctrl_card, bg=self.bg_card)
        mode_frame.pack(fill="x", padx=15, pady=5)
        
        self.radio_midnight = tk.Radiobutton(mode_frame, text="Beijing Midnight (00:00:00)", variable=self.target_mode_var, value="beijing_midnight", bg=self.bg_card, fg=self.fg_white, selectcolor=self.bg_card, activebackground=self.bg_card, activeforeground=self.fg_white, font=("Segoe UI", 9), command=self.update_target_inputs)
        self.radio_midnight.pack(anchor="w")
        
        custom_row = tk.Frame(mode_frame, bg=self.bg_card)
        custom_row.pack(fill="x", pady=(5, 0))
        
        self.radio_custom = tk.Radiobutton(custom_row, text="Custom Target Time (HH:MM:SS):", variable=self.target_mode_var, value="custom", bg=self.bg_card, fg=self.fg_white, selectcolor=self.bg_card, activebackground=self.bg_card, activeforeground=self.fg_white, font=("Segoe UI", 9), command=self.update_target_inputs)
        self.radio_custom.pack(side="left")
        
        self.entry_custom_time = tk.Entry(custom_row, textvariable=self.custom_time_var, bg=self.bg_entry, fg="white", insertbackground="white", relief="flat", justify="center", width=10, font=("Segoe UI", 9))
        self.entry_custom_time.pack(side="left", padx=5)
        
        # Timeshift input and auto calibration RTT
        shift_row = tk.Frame(ctrl_card, bg=self.bg_card)
        shift_row.pack(fill="x", padx=15, pady=(10, 15))
        
        tk.Label(shift_row, text="Timeshift offset (ms):", font=("Segoe UI", 9), fg=self.fg_white, bg=self.bg_card).pack(side="left")
        self.entry_shift = tk.Entry(shift_row, textvariable=self.timeshift_var, bg=self.bg_entry, fg="white", insertbackground="white", relief="flat", justify="center", width=8, font=("Segoe UI", 9))
        self.entry_shift.pack(side="left", padx=5)
        
        self.btn_calibrate = HoverButton(shift_row, self.accent_purple, self.bg_entry, text="Auto-Calibrate RTT", font=("Segoe UI", 8, "bold"), command=self.trigger_calibration)
        self.btn_calibrate.pack(side="right")
        
        # CARD 3: Active configuration status badge
        active_card = tk.Frame(left_col, bg=self.bg_card, bd=1, highlightbackground="#2C2C35", highlightcolor="#2C2C35", highlightthickness=1)
        active_card.pack(fill="both", expand=True, pady=(10, 0))
        
        tk.Label(active_card, text="ACTIVE PARAMETERS", font=("Segoe UI", 9, "bold"), fg=self.accent_blue, bg=self.bg_card).pack(anchor="w", padx=15, pady=(12, 5))
        
        tk.Label(active_card, text="Selected Line index in token.txt:", font=("Segoe UI", 9), fg=self.fg_muted, bg=self.bg_card).pack(anchor="w", padx=15, pady=2)
        self.lbl_selected_line = tk.Label(active_card, text="Line 1", font=("Segoe UI", 10, "bold"), fg=self.fg_white, bg=self.bg_card)
        self.lbl_selected_line.pack(anchor="w", padx=15, pady=(0, 8))
        
        tk.Label(active_card, text="Loaded Token Cookie:", font=("Segoe UI", 9), fg=self.fg_muted, bg=self.bg_card).pack(anchor="w", padx=15, pady=2)
        self.active_token_lbl = tk.Label(active_card, text="None loaded", font=("Consolas", 8), fg=self.accent_green, bg=self.bg_card, wraplength=400, justify="left")
        self.active_token_lbl.pack(anchor="w", padx=15, pady=(0, 12))
        
        # ==========================================
        # RIGHT COLUMN (Interactive Token Manager)
        # ==========================================
        right_col = tk.Frame(body_container, bg=self.bg_dark)
        right_col.pack(side="right", fill="both", expand=True, padx=(10, 0))
        
        token_card = tk.Frame(right_col, bg=self.bg_card, bd=1, highlightbackground="#2C2C35", highlightcolor="#2C2C35", highlightthickness=1)
        token_card.pack(fill="both", expand=True)
        
        tk.Label(token_card, text="VISUAL TOKEN & ACCOUNT MANAGER", font=("Segoe UI", 9, "bold"), fg=self.accent_blue, bg=self.bg_card).pack(anchor="w", padx=15, pady=(12, 10))
        
        # Scrolling treeview frame
        tree_container = tk.Frame(token_card, bg=self.bg_card)
        tree_container.pack(fill="both", expand=True, padx=15, pady=5)
        
        self.tree = ttk.Treeview(tree_container, columns=("line", "token", "timeshift", "status"), show="headings")
        self.tree.heading("line", text="Line")
        self.tree.heading("token", text="Cookie Token")
        self.tree.heading("timeshift", text="Shift (ms)")
        self.tree.heading("status", text="Account State")
        
        self.tree.column("line", width=50, minwidth=40, anchor="center")
        self.tree.column("token", width=250, minwidth=150, anchor="w")
        self.tree.column("timeshift", width=80, minwidth=70, anchor="center")
        self.tree.column("status", width=100, minwidth=80, anchor="center")
        
        scrollbar = ttk.Scrollbar(tree_container, orient="vertical", command=self.tree.yview, style="Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        
        # Table manipulation actions row
        actions_row = tk.Frame(token_card, bg=self.bg_card)
        actions_row.pack(fill="x", padx=15, pady=12)
        
        self.btn_add_tok = HoverButton(actions_row, self.accent_green, self.bg_entry, text="+ Add Token", font=("Segoe UI", 8, "bold"), command=self.add_token_dialog)
        self.btn_add_tok.pack(side="left", padx=(0, 5))
        
        self.btn_edit_tok = HoverButton(actions_row, self.accent_blue, self.bg_entry, text="✏️ Edit Selected", font=("Segoe UI", 8, "bold"), command=self.edit_token_dialog)
        self.btn_edit_tok.pack(side="left", padx=5)
        
        self.btn_delete_tok = HoverButton(actions_row, self.accent_red, self.bg_entry, text="❌ Delete", font=("Segoe UI", 8, "bold"), command=self.delete_token)
        self.btn_delete_tok.pack(side="left", padx=5)
        
        self.btn_verify_tok = HoverButton(actions_row, self.accent_orange, self.bg_entry, text="🔄 Verify Status", font=("Segoe UI", 8, "bold"), command=self.verify_selected_token)
        self.btn_verify_tok.pack(side="right", padx=(5, 0))
        
        self.btn_load_tok = HoverButton(actions_row, self.accent_blue_hover, self.accent_blue, text="⚡ Load to Main", font=("Segoe UI", 8, "bold"), command=self.load_selected_to_inputs)
        self.btn_load_tok.pack(side="right", padx=5)
        
        # Populate table
        self.refresh_token_table()
        
        # ==========================================
        # BOTTOM FRAME (Operations Logs & Master Actions)
        # ==========================================
        console_section = tk.Frame(self.root, bg=self.bg_dark)
        console_section.pack(fill="both", expand=True, padx=20, pady=10)
        
        console_card = tk.Frame(console_section, bg=self.bg_card, bd=1, highlightbackground="#2C2C35", highlightcolor="#2C2C35", highlightthickness=1)
        console_card.pack(side="left", fill="both", expand=True, padx=(0, 10))
        
        tk.Label(console_card, text="REAL-TIME TIMING & NETWORK OPERATIONS LOG", font=("Segoe UI", 9, "bold"), fg=self.accent_blue, bg=self.bg_card).pack(anchor="w", padx=15, pady=(8, 4))
        
        self.console = scrolledtext.ScrolledText(console_card, bg="#0A0A0C", fg="#ECEFF1", insertbackground="white", font=("Consolas", 9), relief="flat", wrap="word", borderwidth=0)
        self.console.pack(fill="both", expand=True, padx=15, pady=(2, 10))
        self.console.config(state="disabled")
        
        # Setup terminal color codes tags
        self.console.tag_config("info", foreground="#ECEFF1")
        self.console.tag_config("success", foreground=self.accent_green)
        self.console.tag_config("warning", foreground=self.accent_orange)
        self.console.tag_config("error", foreground=self.accent_red)
        self.console.tag_config("request", foreground="#80D8FF")
        self.console.tag_config("response", foreground="#EA80FC")
        
        # Master Action Controls Card
        control_actions_frame = tk.Frame(console_section, bg=self.bg_dark, width=200)
        control_actions_frame.pack(side="right", fill="y", padx=(10, 0))
        
        self.btn_main_verify = HoverButton(control_actions_frame, "#303036", self.bg_card, text="VERIFY CREDENTIALS", font=("Segoe UI", 9, "bold"), fg=self.accent_blue, command=self.trigger_verification)
        self.btn_main_verify.pack(fill="x", pady=(0, 8))
        
        self.btn_start = HoverButton(control_actions_frame, "#1B5E20", self.accent_green, text="START AUTOMATION", font=("Segoe UI", 10, "bold"), command=self.trigger_automation)
        self.btn_start.pack(fill="both", expand=True, pady=4)
        
        self.btn_stop = HoverButton(control_actions_frame, self.accent_red, "#801313", text="EMERGENCY STOP", font=("Segoe UI", 10, "bold"), command=self.stop_automation, state="disabled")
        self.btn_stop.pack(fill="x", pady=(8, 0))
        
        # Configure custom inputs disable status
        self.update_target_inputs()

    # ==========================================
    # ACCOUNT DATA MANAGEMENT METHODS
    # ==========================================
    def refresh_token_table(self):
        """Redraws all accounts in the Treeview control."""
        # Clear entries
        for child in self.tree.get_children():
            self.tree.delete(child)
            
        for r in self.records:
            truncated = f"{r['token'][:25]}..." if len(r['token']) > 30 else r['token']
            self.tree.insert("", "end", values=(r["line"], truncated, int(r["timeshift"]), r["status"]))

    def select_account_line(self, line_num):
        """Loads inputs corresponding to specific file line database."""
        target_record = None
        for r in self.records:
            if r["line"] == line_num:
                target_record = r
                break
                
        if target_record:
            self.active_line_var.set(str(line_num))
            self.timeshift_var.set(str(int(target_record["timeshift"])))
            
            self.active_token_lbl.config(text=target_record["token"])
            self.lbl_selected_line.config(text=f"Line {line_num} Loaded")
            with state_lock:
                state["active_token"] = target_record["token"]
                state["active_line"] = line_num
            log_info(f"Loaded Profile Line {line_num}. Local offset: {target_record['timeshift']}ms.")
        else:
            log_error(f"Cannot load profile line {line_num}. Make sure files exist.")

    def on_tree_double_click(self, event):
        """Loads double clicked entry directly to operational parameters."""
        self.load_selected_to_inputs()

    def load_selected_to_inputs(self):
        """Loads selected table item to operational buffers."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Selection Required", "Please select a line in the table first.")
            return
            
        values = self.tree.item(selected[0], "values")
        line_num = int(values[0])
        self.select_account_line(line_num)

    def add_token_dialog(self):
        """Dialog to paste cookie string and timeshift and append to database."""
        token = simpledialog.askstring("Add Token", "Paste your new_bbs_serviceToken Cookie:")
        if not token:
            return
            
        timeshift = simpledialog.askinteger("Timeshift", "Enter baseline timeshift (ms):", initialvalue=1000)
        if timeshift is None:
            timeshift = 1000
            
        new_line = len(self.records) + 1
        self.records.append({
            "line": new_line,
            "token": token.strip(),
            "timeshift": float(timeshift),
            "status": "Not Verified"
        })
        
        if TokenStorage.save_all(self.records):
            log_success(f"Profile added successfully on Line {new_line}.")
            self.refresh_token_table()
            self.select_account_line(new_line)
            play_sound("success")
        else:
            messagebox.showerror("Error", "Failed to write profiles to files.")

    def edit_token_dialog(self):
        """Modifies token content/timeshift for selected records."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Selection", "Select a record in the list first.")
            return
            
        values = self.tree.item(selected[0], "values")
        line_num = int(values[0])
        
        record = next((r for r in self.records if r["line"] == line_num), None)
        if not record:
            return
            
        new_token = simpledialog.askstring("Edit Token", "Edit serviceToken Cookie:", initialvalue=record["token"])
        if new_token is None:
            return
            
        new_shift = simpledialog.askinteger("Edit Timeshift", "Edit timeshift (ms):", initialvalue=int(record["timeshift"]))
        if new_shift is None:
            return
            
        record["token"] = new_token.strip()
        record["timeshift"] = float(new_shift)
        record["status"] = "Not Verified"
        
        if TokenStorage.save_all(self.records):
            log_success(f"Updated Profile #{line_num} successfully.")
            self.refresh_token_table()
            
            # Sync inputs if edited item was active
            with state_lock:
                current_active_line = state["active_line"]
                
            if current_active_line == line_num:
                self.select_account_line(line_num)
            play_sound("success")
        else:
            messagebox.showerror("Error", "Failed to rewrite modifications to disk.")

    def delete_token(self):
        """Removes profile item and re-indexes lines cleanly."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Selection Required", "Select an item to delete first.")
            return
            
        values = self.tree.item(selected[0], "values")
        line_num = int(values[0])
        
        if not messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete profile Line {line_num}?"):
            return
            
        # Remove target
        self.records = [r for r in self.records if r["line"] != line_num]
        
        # Re-index lines sequentially
        for idx, r in enumerate(self.records):
            r["line"] = idx + 1
            
        if TokenStorage.save_all(self.records):
            log_success(f"Profile Line {line_num} deleted. Database re-indexed.")
            self.refresh_token_table()
            
            # Reset active configs
            if self.records:
                self.select_account_line(1)
            else:
                self.active_token_lbl.config(text="")
                self.timeshift_var.set("0")
                with state_lock:
                    state["active_token"] = ""
                    state["active_line"] = 1
            play_sound("warning")
        else:
            messagebox.showerror("Error", "Failed to save files during deletion.")

    def verify_selected_token(self):
        """Runs background API credential checks on highlighted entry."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Selection Required", "Select an entry in the table to verify.")
            return
            
        values = self.tree.item(selected[0], "values")
        line_num = int(values[0])
        
        record = next((r for r in self.records if r["line"] == line_num), None)
        if not record:
            return
            
        with state_lock:
            if state["running"]:
                return
            state["running"] = True
            
        self.btn_verify_tok.config(state="disabled")
        self.lbl_status_badge.config(text="VERIFYING", bg=self.accent_orange)
        
        t = threading.Thread(target=verify_token_bg, args=(record["token"], record, self), daemon=True)
        t.start()

    # ==========================================
    # INPUT & CONTROLS BINDING
    # ==========================================
    def update_target_inputs(self):
        """Enables/Disables entry fields depending on active schedules."""
        if self.target_mode_var.get() == "beijing_midnight":
            self.entry_custom_time.config(state="disabled")
        else:
            self.entry_custom_time.config(state="normal")

    def manual_ntp_sync(self):
        """Button trigger to force timing synchronization."""
        self.lbl_status_badge.config(text="SYNCHRONIZING", bg=self.accent_blue)
        self.btn_manual_sync.config(state="disabled")
        
        def _sync():
            synchronize_time(force_log=True)
            self.root.after(0, lambda: self.btn_manual_sync.config(state="normal"))
            
        threading.Thread(target=_sync, daemon=True).start()

    def trigger_calibration(self):
        """Launches latency probes thread to estimate timeshift."""
        with state_lock:
            calibrating = state["calibrating"]
        if calibrating:
            return
            
        self.btn_calibrate.config(state="disabled")
        self.lbl_status_badge.config(text="CALIBRATING", bg=self.accent_purple)
        
        def _cal():
            run_latency_calibration_bg(self)
            self.root.after(0, lambda: self.btn_calibrate.config(state="normal"))
            
        threading.Thread(target=_cal, daemon=True).start()

    # ==========================================
    # AUTOMATION LAUNCH TRIGGERS
    # ==========================================
    def trigger_verification(self):
        """Active token verification check."""
        with state_lock:
            token = state["active_token"]
            running = state["running"]
            
        if not token:
            messagebox.showerror("Error", "No token cookie loaded to verify.")
            return
        if running:
            return
            
        with state_lock:
            state["running"] = True
            
        self.btn_main_verify.config(state="disabled")
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.lbl_status_badge.config(text="VERIFYING", bg=self.accent_orange)
        
        t = threading.Thread(target=verify_token_bg, args=(token, None, self), daemon=True)
        t.start()

    def trigger_automation(self):
        """Saves current fields, compiles timings, and starts attack worker."""
        # Sync changes from form variables first
        line_num = int(self.active_line_var.get())
        timeshift_str = self.timeshift_var.get().strip()
        
        try:
            timeshift = float(timeshift_str)
        except ValueError:
            messagebox.showerror("Error", "Invalid numeric value entered for timeshift.")
            return
            
        with state_lock:
            state["target_mode"] = self.target_mode_var.get()
            state["custom_target_time"] = self.custom_time_var.get().strip()
            token = state["active_token"]
            running = state["running"]
            
        if not token:
            messagebox.showerror("Error", "Please load a valid account profile before starting.")
            return
        if running:
            return
            
        # Overwrite file values to ensure persistent modifications
        for r in self.records:
            if r["line"] == line_num:
                r["timeshift"] = timeshift
                break
        TokenStorage.save_all(self.records)
        self.refresh_token_table()
        
        with state_lock:
            state["running"] = True
            
        self.btn_main_verify.config(state="disabled")
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        
        # Calculate target state
        target_bj, _ = calculate_target_timestamps()
        
        log_info(f"Loaded cookie token index: {line_num}")
        log_info(f"Target system scheduling: {state['target_mode']}")
        
        # Spawn daemon worker
        t = threading.Thread(target=run_unlock_automation_bg, args=(token, timeshift), daemon=True)
        t.start()

    def stop_automation(self):
        """Immediately aborts all thread cycles cleanly."""
        with state_lock:
            state["running"] = False
        log_warning("Immediate emergency stop requested. Processing abort sequence...")

    # ==========================================
    # SYSTEM POLLECTION LOOP (60 FPS RTT)
    # ==========================================
    def update_loop(self):
        """Thread-safe UI polling loop updating time statistics and draining logs."""
        # 1. Check thread active state
        with state_lock:
            running = state["running"]
            calibrating = state["calibrating"]
            synced = state["is_synced"]
            countdown_str = state["countdown_str"]
            beijing_time_str = state["beijing_time_str"]
            
        # Adjust header status badge
        if running:
            # Check if attack mode is active or waiting
            if countdown_str == "00:00:00.000":
                self.lbl_status_badge.config(text="ATTACKING", bg=self.accent_purple)
            else:
                self.lbl_status_badge.config(text="WAITING", bg=self.accent_orange)
        elif calibrating:
            self.lbl_status_badge.config(text="CALIBRATING", bg=self.accent_purple)
        elif synced:
            self.lbl_status_badge.config(text="SYNCHRONIZED", bg=self.accent_green)
        else:
            self.lbl_status_badge.config(text="READY", bg=self.accent_green)
            
        # 2. Update real-time clock tickers
        if synced:
            # Beijing clock ticks live if synced
            now_bj = get_synchronized_beijing_time()
            self.lbl_beijing_clock.config(text=now_bj.strftime('%H:%M:%S.%f')[:-3])
            
            # Print latency statistics of servers
            with state_lock:
                details = dict(state["ntp_server_details"])
            if details:
                min_srv = min(details.keys(), key=lambda k: details[k]["rtt"])
                latency = details[min_srv]["rtt"]
                offset = details[min_srv]["offset"]
                self.lbl_sync_indicator.config(text=f"CONNECTED TO NTP (RTT: {latency:.1f}ms | Offset: {offset:+.1f}ms)", fg=self.accent_green)
        else:
            self.lbl_beijing_clock.config(text="--:--:--.---")
            self.lbl_sync_indicator.config(text="CLOCK DRIFT NOT CALCULATED", fg=self.accent_red)
            
        # Countdown display refresh
        self.lbl_countdown.config(text=countdown_str)
        
        # 3. Master button active triggers toggling
        if running:
            self.btn_start.config(state="disabled")
            self.btn_main_verify.config(state="disabled")
            self.btn_stop.config(state="normal")
            
            self.btn_add_tok.config(state="disabled")
            self.btn_edit_tok.config(state="disabled")
            self.btn_delete_tok.config(state="disabled")
            self.btn_verify_tok.config(state="disabled")
            self.btn_load_tok.config(state="disabled")
            self.btn_calibrate.config(state="disabled")
            self.btn_manual_sync.config(state="disabled")
        else:
            self.btn_start.config(state="normal")
            self.btn_main_verify.config(state="normal")
            self.btn_stop.config(state="disabled")
            
            self.btn_add_tok.config(state="normal")
            self.btn_edit_tok.config(state="normal")
            self.btn_delete_tok.config(state="normal")
            self.btn_verify_tok.config(state="normal")
            self.btn_load_tok.config(state="normal")
            if not calibrating:
                self.btn_calibrate.config(state="normal")
            self.btn_manual_sync.config(state="normal")
            
        # 4. Drain thread logger queue
        logs_to_print = []
        while not state["log_queue"].empty():
            try:
                logs_to_print.append(state["log_queue"].get_nowait())
            except queue.Empty:
                break
                
        if logs_to_print:
            self.console.config(state="normal")
            for tag, text in logs_to_print:
                self.console.insert("end", text, tag)
            self.console.see("end")
            self.console.config(state="disabled")
            
        # Poll UI every 15ms (~60fps rendering speed)
        self.root.after(15, self.update_loop)

# ==========================================
# APPLICATION LAUNCHER ENTRY
# ==========================================
if __name__ == "__main__":
    # Standard Tkinter main thread loop launcher
    root = tk.Tk()
    app = App(root)
    root.mainloop()