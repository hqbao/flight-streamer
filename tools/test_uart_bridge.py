#!/usr/bin/env python3
"""
UART-WiFi Bridge Tester

Tests data transmission between two ESP32 flight-streamer modules connected
via WiFi (one AP, one STA). Communicates with each device via USB-UART only —
the ESP32s handle the WiFi link between themselves automatically.

Data flow (Send A → B):
  Tool → UART-A → ESP32-A → WiFi/UDP → ESP32-B → UART-B → Tool

Setup:
  1. Flash Device A with ENABLE_WIFI_AP=1 (AP mode)
  2. Flash Device B with ENABLE_WIFI_AP=0, WIFI_STA_SSID="SkyDrone"
  3. Connect both devices to laptop via USB
  4. Run: python3 test_uart_bridge.py

Dependencies: pyserial (pip install pyserial)
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import serial
import serial.tools.list_ports
import struct
import threading
import time
from datetime import datetime

# =============================================================================
# DB Protocol
# =============================================================================

DB_HEADER = 6   # 'd' 'b' [id] [sub] [len_lo] [len_hi]
DB_FOOTER = 2   # [ck_lo] [ck_hi]  (16-bit sum, little-endian)
DB_ID_TEST = 0x01
BAUD = 9600


def build_db_packet(msg_id, sub_id, payload):
    """Build DB packet: ['d']['b'][ID][SubID][len_lo][len_hi][payload][ck_lo][ck_hi]"""
    hdr = bytes([0x64, 0x62, msg_id, sub_id]) + struct.pack('<H', len(payload))
    body = hdr + payload
    cksum = sum(body[2:]) & 0xFFFF
    return body + struct.pack('<H', cksum)


def verify_db_packet(data):
    """Verify and parse DB packet. Returns (id, sub_id, payload) or None."""
    if len(data) < DB_HEADER + DB_FOOTER:
        return None
    if data[0] != 0x64 or data[1] != 0x62:
        return None
    plen = data[4] | (data[5] << 8)
    total = DB_HEADER + plen + DB_FOOTER
    if len(data) < total:
        return None
    cksum = sum(data[2:DB_HEADER + plen]) & 0xFFFF
    rx_ck = data[DB_HEADER + plen] | (data[DB_HEADER + plen + 1] << 8)
    if cksum != rx_ck:
        return None
    return data[2], data[3], bytes(data[DB_HEADER:DB_HEADER + plen])


def hex_fmt(data, limit=36):
    """Format bytes as hex string, truncated."""
    h = ' '.join(f'{b:02X}' for b in data[:limit])
    return h + (' ...' if len(data) > limit else '') + f' ({len(data)}B)'


# =============================================================================
# Application
# =============================================================================

class App:
    def __init__(self, root):
        self.root = root
        root.title('UART-WiFi Bridge Tester')
        root.geometry('1050x680')
        root.minsize(900, 550)

        self.ser = {'a': None, 'b': None}
        self.running = True
        self.seq = 0
        self.stats = {k: 0 for k in ('a_tx', 'a_rx', 'b_tx', 'b_rx')}

        self._build_ui()
        self.refresh_ports()

    # -----------------------------------------------------------------
    # UI
    # -----------------------------------------------------------------
    def _build_ui(self):
        # -- Connections --
        cf = ttk.LabelFrame(self.root, text='Connections', padding=8)
        cf.pack(fill='x', padx=8, pady=4)

        self.port_var = {}
        self.btn_conn = {}
        self.lbl_status = {}
        self.combo_port = {}

        for dev, label in [('a', 'Device A (AP)'), ('b', 'Device B (STA)')]:
            row = ttk.Frame(cf)
            row.pack(fill='x', pady=1)
            ttk.Label(row, text=f'{label}:', width=16).pack(side='left')

            ttk.Label(row, text='UART:').pack(side='left', padx=(4, 2))
            self.port_var[dev] = tk.StringVar()
            self.combo_port[dev] = ttk.Combobox(
                row, textvariable=self.port_var[dev], width=24, state='readonly')
            self.combo_port[dev].pack(side='left', padx=2)

            self.btn_conn[dev] = ttk.Button(
                row, text='Connect', command=lambda d=dev: self.toggle_connect(d))
            self.btn_conn[dev].pack(side='left', padx=4)

            self.lbl_status[dev] = ttk.Label(row, text='● Disconnected', foreground='gray')
            self.lbl_status[dev].pack(side='left', padx=4)

        ctrl = ttk.Frame(cf)
        ctrl.pack(fill='x', pady=2)
        ttk.Button(ctrl, text='↻ Refresh Ports', command=self.refresh_ports).pack(side='left')

        # -- Send --
        sf = ttk.LabelFrame(self.root, text='Send Test Data', padding=8)
        sf.pack(fill='x', padx=8, pady=4)

        r1 = ttk.Frame(sf)
        r1.pack(fill='x', pady=2)
        ttk.Label(r1, text='Message:').pack(side='left')
        self.msg_var = tk.StringVar(value='Hello Bridge')
        ttk.Entry(r1, textvariable=self.msg_var, width=35).pack(
            side='left', padx=4, fill='x', expand=True)

        r2 = ttk.Frame(sf)
        r2.pack(fill='x', pady=4)
        ttk.Button(r2, text='▶ Send A → B',
                   command=lambda: self.send_test('a')).pack(side='left', padx=4)
        ttk.Button(r2, text='◀ Send B → A',
                   command=lambda: self.send_test('b')).pack(side='left', padx=4)

        ttk.Separator(r2, orient='vertical').pack(side='left', padx=8, fill='y')

        self.auto_var = tk.BooleanVar()
        ttk.Checkbutton(r2, text='Auto:', variable=self.auto_var,
                        command=self.toggle_auto).pack(side='left')
        self.interval_var = tk.StringVar(value='1000')
        ttk.Entry(r2, textvariable=self.interval_var, width=5).pack(side='left', padx=2)
        ttk.Label(r2, text='ms').pack(side='left')

        self.auto_dir_var = tk.StringVar(value='A → B')
        ttk.Combobox(r2, textvariable=self.auto_dir_var, width=6,
                     values=['A → B', 'B → A', 'Both'], state='readonly').pack(side='left', padx=4)

        self.lbl_stats = ttk.Label(r2, text='')
        self.lbl_stats.pack(side='right', padx=8)

        # -- Logs --
        lf = ttk.Frame(self.root)
        lf.pack(fill='both', expand=True, padx=8, pady=4)
        lf.columnconfigure(0, weight=1)
        lf.columnconfigure(1, weight=1)

        self.logs = {}
        for col, (dev, title) in enumerate([('a', 'Device A (AP)'), ('b', 'Device B (STA)')]):
            frame = ttk.LabelFrame(lf, text=title)
            frame.grid(row=0, column=col, sticky='nsew', padx=2)
            txt = scrolledtext.ScrolledText(
                frame, height=18, font=('Courier', 10), state='disabled', wrap='word')
            txt.pack(fill='both', expand=True)
            txt.tag_configure('tx', foreground='#1976D2')
            txt.tag_configure('rx', foreground='#388E3C')
            txt.tag_configure('err', foreground='#D32F2F')
            txt.tag_configure('info', foreground='#757575')
            self.logs[dev] = txt

        bf = ttk.Frame(self.root)
        bf.pack(fill='x', padx=8, pady=(0, 4))
        ttk.Button(bf, text='Clear A', command=lambda: self.clear_log('a')).pack(side='left', padx=2)
        ttk.Button(bf, text='Clear B', command=lambda: self.clear_log('b')).pack(side='left', padx=2)
        ttk.Button(bf, text='Clear All',
                   command=lambda: [self.clear_log(d) for d in 'ab']).pack(side='left', padx=2)
        ttk.Button(bf, text='Reset Stats', command=self.reset_stats).pack(side='right', padx=2)

        self._update_stats()

    # -----------------------------------------------------------------
    # Port management
    # -----------------------------------------------------------------
    def refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        for dev in 'ab':
            prev = self.port_var[dev].get()
            self.combo_port[dev]['values'] = ports
            if prev in ports:
                self.port_var[dev].set(prev)
        if ports and not self.port_var['a'].get():
            self.port_var['a'].set(ports[0])
        if len(ports) > 1 and not self.port_var['b'].get():
            self.port_var['b'].set(ports[-1])

    # -----------------------------------------------------------------
    # Connect / Disconnect
    # -----------------------------------------------------------------
    def toggle_connect(self, dev):
        if self.ser[dev]:
            self._disconnect(dev)
        else:
            self._connect(dev)

    def _connect(self, dev):
        port = self.port_var[dev].get()
        if not port:
            self._log(dev, 'No port selected', 'err')
            return

        other = 'b' if dev == 'a' else 'a'
        if self.ser[other] and self.port_var[other].get() == port:
            self._log(dev, 'Error: same port as other device', 'err')
            return

        try:
            self.ser[dev] = serial.Serial(port, BAUD, timeout=0.1)
            self.btn_conn[dev].config(text='Disconnect')
            self.lbl_status[dev].config(text='● Connected', foreground='#388E3C')
            self.combo_port[dev].config(state='disabled')
            self._log(dev, f'Connected: {port} @ {BAUD} baud', 'info')

            threading.Thread(target=self._uart_rx, args=(dev,), daemon=True).start()
        except Exception as e:
            self._log(dev, f'Connect failed: {e}', 'err')

    def _disconnect(self, dev):
        if self.ser[dev]:
            try:
                self.ser[dev].close()
            except Exception:
                pass
            self.ser[dev] = None
        self.btn_conn[dev].config(text='Connect')
        self.lbl_status[dev].config(text='● Disconnected', foreground='gray')
        self.combo_port[dev].config(state='readonly')
        self._log(dev, 'Disconnected', 'info')

    # -----------------------------------------------------------------
    # Send test packet via UART
    # -----------------------------------------------------------------
    def send_test(self, from_dev):
        if not self.ser[from_dev]:
            self._log(from_dev, 'Not connected', 'err')
            return

        self.seq += 1
        msg = self.msg_var.get()

        # Payload: [seq:u32][timestamp_ms:u32][text]
        payload = struct.pack('<II', self.seq, int(time.time() * 1000) & 0xFFFFFFFF)
        payload += msg.encode('utf-8')

        pkt = build_db_packet(DB_ID_TEST, 0x00, payload)

        try:
            self.ser[from_dev].write(pkt)
            target = 'B' if from_dev == 'a' else 'A'
            self._log(from_dev, f'TX → {target}: seq={self.seq} "{msg}"', 'tx')
            self._log(from_dev, f'  {hex_fmt(pkt)}', 'tx')
            self.stats[f'{from_dev}_tx'] += 1
            self._update_stats()
        except Exception as e:
            self._log(from_dev, f'Write error: {e}', 'err')

    # -----------------------------------------------------------------
    # UART RX — parse DB packets from byte stream
    # -----------------------------------------------------------------
    def _uart_rx(self, dev):
        """Byte-by-byte DB parser (mirrors ESP32 uart_server parser)."""
        buf = bytearray()
        stage = 0
        plen = 0

        while self.running:
            s = self.ser[dev]
            if not s or not s.is_open:
                break
            try:
                chunk = s.read(256)
                if not chunk:
                    continue

                for b in chunk:
                    if stage == 0:
                        if b == 0x64:  # 'd'
                            buf = bytearray([b])
                            stage = 1
                    elif stage == 1:
                        if b == 0x62:  # 'b'
                            buf.append(b)
                            stage = 2
                        else:
                            stage = 0
                    elif stage in (2, 3, 4):
                        buf.append(b)
                        stage += 1
                    elif stage == 5:
                        buf.append(b)
                        plen = buf[4] | (buf[5] << 8)
                        if plen > 240:
                            stage = 0
                        else:
                            stage = 6
                    elif stage == 6:
                        buf.append(b)
                        if len(buf) >= DB_HEADER + plen + DB_FOOTER:
                            self._on_uart_pkt(dev, bytes(buf))
                            stage = 0
                    else:
                        stage = 0

            except serial.SerialException:
                self._log_safe(dev, 'Serial disconnected', 'err')
                break
            except Exception:
                continue

    def _on_uart_pkt(self, dev, raw):
        """Handle complete DB packet received on UART."""
        parsed = verify_db_packet(raw)
        if not parsed:
            self._log_safe(dev, f'RX bad checksum: {hex_fmt(raw)}', 'err')
            return

        mid, sid, payload = parsed

        # Decode test packet
        info = f'id=0x{mid:02X} sub=0x{sid:02X} len={len(payload)}'
        if mid == DB_ID_TEST and len(payload) >= 8:
            seq = struct.unpack('<I', payload[:4])[0]
            ts = struct.unpack('<I', payload[4:8])[0]
            text = payload[8:].decode('utf-8', errors='replace')
            now_ms = int(time.time() * 1000) & 0xFFFFFFFF
            lat = now_ms - ts
            if lat < 0:
                lat += 0x100000000
            info = f'seq={seq} "{text}" latency={lat}ms'

        src = 'A' if dev == 'b' else 'B'
        self._log_safe(dev, f'RX ← {src}: {info}', 'rx')
        self._log_safe(dev, f'  {hex_fmt(raw)}', 'rx')
        self.stats[f'{dev}_rx'] += 1
        self.root.after(0, self._update_stats)

    # -----------------------------------------------------------------
    # Auto send
    # -----------------------------------------------------------------
    def toggle_auto(self):
        if self.auto_var.get():
            self._auto_tick()

    def _auto_tick(self):
        if not self.auto_var.get() or not self.running:
            return

        direction = self.auto_dir_var.get()
        if direction == 'A → B' and self.ser['a']:
            self.send_test('a')
        elif direction == 'B → A' and self.ser['b']:
            self.send_test('b')
        elif direction == 'Both':
            if self.ser['a']:
                self.send_test('a')
            if self.ser['b']:
                self.send_test('b')

        try:
            ms = max(100, int(self.interval_var.get()))
        except ValueError:
            ms = 1000
        self.root.after(ms, self._auto_tick)

    # -----------------------------------------------------------------
    # Logging
    # -----------------------------------------------------------------
    def _log(self, dev, msg, tag='info'):
        """Log message (main thread only)."""
        log = self.logs[dev]
        ts = datetime.now().strftime('%H:%M:%S.') + f'{datetime.now().microsecond // 1000:03d}'
        log.config(state='normal')
        log.insert('end', f'[{ts}] {msg}\n', tag)
        log.see('end')
        log.config(state='disabled')

    def _log_safe(self, dev, msg, tag='info'):
        """Thread-safe log via root.after."""
        self.root.after(0, lambda d=dev, m=msg, t=tag: self._log(d, m, t))

    def clear_log(self, dev):
        self.logs[dev].config(state='normal')
        self.logs[dev].delete('1.0', 'end')
        self.logs[dev].config(state='disabled')

    def _update_stats(self):
        s = self.stats
        self.lbl_stats.config(
            text=f"A: TX={s['a_tx']} RX={s['a_rx']}  │  "
                 f"B: TX={s['b_tx']} RX={s['b_rx']}")

    def reset_stats(self):
        self.stats = {k: 0 for k in self.stats}
        self.seq = 0
        self._update_stats()

    # -----------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------
    def on_closing(self):
        self.running = False
        for dev in 'ab':
            if self.ser[dev]:
                try:
                    self.ser[dev].close()
                except Exception:
                    pass
        self.root.destroy()


# =============================================================================
# Main
# =============================================================================

def main():
    root = tk.Tk()
    app = App(root)
    root.protocol('WM_DELETE_WINDOW', app.on_closing)
    root.mainloop()


if __name__ == '__main__':
    main()
