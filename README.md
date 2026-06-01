# HyperOS Unlock Tool Script🚀

A highly optimized, zero-dependency automation suite designed to help you secure Xiaomi Bootloader Unlock permission for HyperOS (Global). 

This tool synchronizes your local clock with precise Network Time Protocol (NTP) servers, runs real-time millisecond-accurate countdowns, measures network round-trip delay, and executes high-speed keep-alive HTTP requests exactly at the turn of the target trigger time (e.g. Beijing Midnight) to maximize your chances of obtaining the unlock quota.

---

## ⚡ Outstanding Features

*   **Interactive Token & Account Manager GUI**: Avoid manually editing text files. Built-in modern table view lets you Add, Edit, Delete, Verify status, and double-click to Load account cookies smoothly. Fully backward-compatible with `token.txt` and `timeshift.txt` files.
*   **Network Auto-Latency Calibrator**: Executes lightweight background HTTP probe requests to the Xiaomi API endpoint (`sgp-api.buy.mi.com`) to measure average network round-trip time (RTT) and jitter, calculating and recommending the mathematically optimal `timeshift` warm-up value with a single click.
*   **Microsecond-Accurate NTP Sync**: Queries raw UDP packets from multiple global NTP time servers, filtering network jitter to calculate highly accurate local clock offsets.
*   **Continuous Drift Control**: Automatically triggers a silent NTP synchronization every 10 minutes in the background to counteract Windows system clock drift.
*   **Custom Target Scheduling**: Choose between standard **Beijing Midnight (00:00:00)** or schedule a **Custom Target Time** (in `HH:MM:SS` Beijing Time) for testing.
*   **Sleek Dark UI Aesthetics**: Styled with high-contrast, slate-dark professional flat aesthetics, colored operational status badges (`READY`, `SYNCHRONIZING`, `WAITING`, `ATTACKING`, `VERIFYING`), and smooth hover-responsive controls.
*   **Dynamic Background Audio Cues**: Features non-blocking Windows chimes (`winsound`) alerting you differently on **Success, Warnings, Errors, and Trigger releases** so you don't have to keep staring at the screen.
*   **Persistent Diagnostics**: Automatically records all network dispatches, response delay, and operations to a local file `hyperos_unlock.log`.

---

## 🛠️ Requirements & Installation

This project is built under the **Zero-Dependency** philosophy, requiring **only** standard built-in Python libraries. 

### Option 1: Standalone Windows Executable (.exe)
You can run the application **without installing Python or any libraries**:
1. Download the pre-compiled `HyperOS_Unlock_Tool.exe` from the **Releases** section on the GitHub repository.
2. Double-click the executable to launch the tool immediately.

*Note: If you wish to build the executable yourself:*
1. Install PyInstaller:
   ```bash
   pip install pyinstaller
   ```
2. Build using the provided spec file:
   ```bash
   pyinstaller HyperOS_Unlock_Tool.spec
   ```
3. Find the compiled `HyperOS_Unlock_Tool.exe` in the `dist/` directory.

### Option 2: Running via Python Script
To run it using your local Python installation:
1. Ensure you have Python 3.8 or higher installed.
2. (Linux/macOS only) Ensure `tkinter` is installed (e.g., `sudo apt install python3-tk`).
3. Execute the script in your terminal:
   ```bash
   python HyperOS_Unlock_Script.py
   ```

---

## 📋 Data Structure & Compatibility

The visual Account Manager stores profiles locally in your project root using two simple text files to remain 100% backward compatible:
*   `token.txt`: Stores your `new_bbs_serviceToken` cookies on a per-line basis.
*   `timeshift.txt`: Stores the corresponding integer timeshift offsets (in milliseconds) on a per-line basis.

---

## 🔒 Security & Privacy

Your account cookies are highly sensitive credentials. This repository is configured with a secure `.gitignore` file that **completely excludes** the following files from Git tracking:
*   `token.txt` & `timeshift.txt` (Private accounts)
*   `*.log` (Session records)
*   `build/`, `dist/`, `*.spec` (Local PyInstaller builds)

---

## 📜 Disclaimer
This tool is intended for personal and educational use to assist with scheduling manual actions. Please use responsibly and ensure you comply with Xiaomi's account terms of service.
