#!/usr/bin/env python3
"""
DBLink UART<->WiFi Bridge Tester.

Connects to two ESP32-S3 dblink boards over USB-UART (one AP, one STA)
and exercises the WiFi/UDP bridge between them with a small DB-framed
test packet. Useful for validating end-to-end byte transport without
the heavier bandwidth-lab harness.

Setup:
  1. Flash Device A with ENABLE_WIFI_AP=1.
  2. Flash Device B with ENABLE_WIFI_AP=0.
  3. Connect both via USB; pick their ports below; Connect both;
     then Send A -> B / Send B -> A or enable Auto.

UI style: flight-controller/tools/pytools/_ui.py (shared dashboard primitives) —
a sibling checkout of that repository is REQUIRED; see tools/_fc_pytools.py.
"""

from __future__ import annotations

import os
import queue
import struct
import sys
import threading
import time
from collections import deque
from datetime import datetime

import matplotlib
if not os.environ.get('MPLBACKEND'):
    matplotlib.use('MacOSX' if sys.platform == 'darwin' else 'TkAgg')
import matplotlib.pyplot as plt           # noqa: E402
from matplotlib.widgets import TextBox     # noqa: E402

import serial                              # noqa: E402
import serial.tools.list_ports             # noqa: E402

# The dashboard primitives live in the sibling flight-controller repository;
# _fc_pytools resolves that cross-repository path and, if it is absent or has
# moved, exits with the path it tried instead of a bare ModuleNotFoundError.
from _fc_pytools import add_flight_controller_pytools   # noqa: E402
add_flight_controller_pytools()
from _ui import (                          # noqa: E402
    apply_theme, make_figure, add_panel, make_button, restyle_button,
    make_footer, screen_fit_figsize,
    PANEL, PANEL_EDGE, TEXT, TEXT_DIM, TEXT_FAINT, GOOD,
)


# =============================================================================
# DB protocol
# =============================================================================
DB_HEADER  = 6   # 'd' 'b' [id] [sub] [len_lo] [len_hi]
DB_FOOTER  = 2   # [ck_lo] [ck_hi]  (16-bit sum LE)
DB_ID_TEST = 0x01
BAUD       = 115200


def build_db_packet(msg_id, sub_id, payload):
    hdr = bytes([0x64, 0x62, msg_id, sub_id]) + struct.pack('<H', len(payload))
    body = hdr + payload
    cksum = sum(body[2:]) & 0xFFFF
    return body + struct.pack('<H', cksum)


def verify_db_packet(data):
    if len(data) < DB_HEADER + DB_FOOTER:    return None
    if data[0] != 0x64 or data[1] != 0x62:   return None
    plen = data[4] | (data[5] << 8)
    total = DB_HEADER + plen + DB_FOOTER
    if len(data) < total:                    return None
    cksum = sum(data[2:DB_HEADER + plen]) & 0xFFFF
    rx_ck = data[DB_HEADER + plen] | (data[DB_HEADER + plen + 1] << 8)
    if cksum != rx_ck:                       return None
    return data[2], data[3], bytes(data[DB_HEADER:DB_HEADER + plen])


def hex_fmt(data, limit=24):
    h = ' '.join(f'{b:02X}' for b in data[:limit])
    return h + (' ...' if len(data) > limit else '') + f' ({len(data)}B)'


def _open_serial_autobaud(port, timeout=1.0, magic=b'db',
                          bauds=(115200, 38400, 9600, 230400, 460800, 921600),
                          probe_time=0.5):
    """Open `port`, sniff each baud for `magic`; fall back to bauds[0]."""
    for b in bauds:
        try:
            s = serial.Serial(port, b, timeout=0.05)
        except Exception:
            continue
        try:
            s.reset_input_buffer()
        except Exception:
            pass
        deadline = time.time() + probe_time
        buf = bytearray()
        hit = False
        while time.time() < deadline:
            chunk = s.read(256)
            if chunk:
                buf.extend(chunk)
                if magic in buf:
                    hit = True; break
                if len(buf) > 4096:
                    del buf[:-1024]
        if hit:
            s.timeout = timeout
            try: s.reset_input_buffer()
            except Exception: pass
            return s, b
        s.close()
    return serial.Serial(port, bauds[0], timeout=timeout), bauds[0]


# =============================================================================
# Background worker
# =============================================================================
class Worker:
    """Owns both serial ports; reads from each on a daemon thread, emits
    events into ``ev_queue`` so the UI thread can render."""

    def __init__(self, ev_queue):
        self.ev = ev_queue
        self.ser = {'a': None, 'b': None}
        self.baud = {'a': BAUD, 'b': BAUD}
        self._stop = {'a': False, 'b': False}
        self._threads = {}

    def is_open(self, dev): return self.ser[dev] is not None

    def connect(self, dev, port):
        # Run actual open off-thread so the UI never blocks on launch / click.
        def _do_open():
            try:
                s = serial.Serial(port, BAUD, timeout=0.1)
            except Exception as e:
                self.ev.put(('log', dev, f'open failed: {e}', 'err'))
                return
            self.ser[dev] = s; self.baud[dev] = BAUD
            self._stop[dev] = False
            t = threading.Thread(target=self._reader, args=(dev,),
                                 daemon=True)
            self._threads[dev] = t; t.start()
            self.ev.put(('conn', dev, True, port, BAUD))
        threading.Thread(target=_do_open, daemon=True).start()

    def disconnect(self, dev):
        self._stop[dev] = True
        s = self.ser[dev]; self.ser[dev] = None
        if s:
            try: s.close()
            except Exception: pass
        self.ev.put(('conn', dev, False, None, None))

    def write(self, dev, data):
        s = self.ser[dev]
        if s is None:
            self.ev.put(('log', dev, 'not connected', 'err'))
            return 0
        try:
            return s.write(data) or len(data)
        except Exception as e:
            self.ev.put(('log', dev, f'write error: {e}', 'err'))
            return 0

    def _reader(self, dev):
        """Byte-by-byte DB parser (mirrors uart_server)."""
        buf = bytearray()
        stage = 0
        plen = 0
        while not self._stop[dev]:
            s = self.ser[dev]
            if s is None: break
            try:
                chunk = s.read(256)
            except serial.SerialException:
                self.ev.put(('log', dev, 'serial disconnected', 'err'))
                break
            except Exception:
                continue
            if not chunk: continue
            for b in chunk:
                if stage == 0:
                    if b == 0x64: buf = bytearray([b]); stage = 1
                elif stage == 1:
                    if b == 0x62: buf.append(b); stage = 2
                    else: stage = 0
                elif stage in (2, 3, 4):
                    buf.append(b); stage += 1
                elif stage == 5:
                    buf.append(b)
                    plen = buf[4] | (buf[5] << 8)
                    stage = 6 if plen <= 240 else 0
                elif stage == 6:
                    buf.append(b)
                    if len(buf) >= DB_HEADER + plen + DB_FOOTER:
                        self.ev.put(('rx', dev, bytes(buf)))
                        stage = 0
                else:
                    stage = 0


# =============================================================================
# Dashboard
# =============================================================================
def _style_textbox(tb):
    try:
        tb.color = PANEL
        tb.hovercolor = PANEL_EDGE
        tb.label.set_color(TEXT_DIM); tb.label.set_fontsize(8.5)
        tb.text_disp.set_color(TEXT); tb.text_disp.set_fontsize(9)
        tb.cursor.set_color(TEXT)
        ax = tb.ax
        ax.set_facecolor(PANEL)
        for sp in ax.spines.values():
            sp.set_edgecolor(PANEL_EDGE); sp.set_linewidth(0.8)
    except Exception:
        pass


# See dblink/tools/bandwidth_lab.py for the same filter.
_PORT_HIDE    = ('debug-console', 'Bluetooth', 'wlan-debug', 'wireless')
_PORT_PREFER  = ('usbmodem', 'usbserial', 'SLAB_USB', 'ttyACM', 'ttyUSB')


def list_data_ports():
    """Real USB CDC ports (debug-console etc. hidden)."""
    out = []
    for p in serial.tools.list_ports.comports():
        dev = p.device
        if any(h in dev for h in _PORT_HIDE):
            continue
        out.append(dev)
    out.sort(key=lambda d: (0 if any(k in d for k in _PORT_PREFER) else 1,
                             d))
    return out


def build_dashboard():
    apply_theme()
    matplotlib.rcParams['toolbar'] = 'None'

    figsize = screen_fit_figsize(14.0, 8.4)
    fig, _ = make_figure(
        'DBLink UART<->WiFi Bridge Tester',
        subtitle='DB-framed end-to-end packet test between AP and STA',
        size=figsize,
    )

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    # Top row: Devices card  +  Send card
    dev_card  = (0.012, 0.700, 0.395, 0.220)
    send_card = (0.413, 0.700, 0.575, 0.220)

    # Two log cards side by side, fill the rest.
    log_a_card = (0.012, 0.040, 0.488, 0.640)
    log_b_card = (0.500, 0.040, 0.488, 0.640)

    add_panel(fig, dev_card,  title='DEVICES')
    add_panel(fig, send_card, title='SEND TEST DATA')
    add_panel(fig, log_a_card, title='DEVICE A  (AP)')
    add_panel(fig, log_b_card, title='DEVICE B  (STA)')

    # ------------------------------------------------------------------
    # DEVICES card
    # ------------------------------------------------------------------
    dev_meta = {}

    def _row(dev, label, y_top):
        fig.text(dev_card[0] + 0.012, y_top - 0.018, label,
                 fontsize=9, fontweight='bold', color=TEXT,
                 family='monospace', va='top')
        port_x = dev_card[0] + 0.080
        port_w = 0.180
        port_y = y_top - 0.045
        port_h = 0.034
        port_btn = make_button(fig, (port_x, port_y, port_w, port_h),
                                '(no ports)', kind='default')
        port_btn._ui_ax.set_zorder(3.0)
        port_btn.label.set_fontsize(8.5)
        btn = make_button(fig, (port_x + port_w + 0.006, port_y, 0.095, port_h),
                          'Connect', kind='primary')
        btn._ui_ax.set_zorder(3.0)
        dot = fig.text(port_x + port_w + 0.115, port_y + port_h * 0.5,
                       '\u25CF', fontsize=12, color=TEXT_FAINT,
                       ha='left', va='center')
        dev_meta[dev] = {'port_btn': port_btn, 'port': '',
                          'btn': btn, 'dot': dot}

    _row('a', 'AP',  dev_card[1] + dev_card[3] - 0.020)
    _row('b', 'STA', dev_card[1] + dev_card[3] - 0.090)

    btn_refresh = make_button(fig, (dev_card[0] + 0.012,
                                     dev_card[1] + 0.012, 0.095, 0.034),
                              'Refresh', kind='default')
    btn_refresh._ui_ax.set_zorder(3.0)

    # ------------------------------------------------------------------
    # SEND card
    # ------------------------------------------------------------------
    # Message text-box (wide).
    msg_y = send_card[1] + send_card[3] - 0.060
    fig.text(send_card[0] + 0.012, msg_y + 0.030, 'MESSAGE',
             fontsize=8.5, color=TEXT_DIM, family='monospace', va='top')
    ax_msg = fig.add_axes((send_card[0] + 0.012, msg_y,
                           send_card[2] - 0.024, 0.034))
    ax_msg.set_zorder(3.0)
    tb_msg = TextBox(ax_msg, '', initial='Hello Bridge',
                     color=PANEL, hovercolor=PANEL_EDGE)
    _style_textbox(tb_msg)

    # Action row: [Send A->B] [Send B->A] | [Auto toggle] [Interval ms]
    act_y = send_card[1] + 0.075
    btn_w = 0.105
    btn_h = 0.040
    bx = send_card[0] + 0.012
    btn_sa = make_button(fig, (bx, act_y, btn_w, btn_h),
                         'Send A -> B', kind='primary')
    btn_sb = make_button(fig, (bx + btn_w + 0.010, act_y, btn_w, btn_h),
                         'Send B -> A', kind='primary')
    btn_auto = make_button(fig,
                            (bx + 2 * (btn_w + 0.010) + 0.020, act_y,
                             btn_w, btn_h),
                            'Auto: OFF', kind='default')
    for b in (btn_sa, btn_sb, btn_auto):
        b._ui_ax.set_zorder(3.0)

    # Interval textbox + label
    int_x = bx + 3 * (btn_w + 0.010) + 0.025
    fig.text(int_x, act_y + btn_h + 0.004, 'INTERVAL ms',
             fontsize=8.5, color=TEXT_DIM, family='monospace', va='bottom')
    ax_int = fig.add_axes((int_x, act_y, 0.065, btn_h))
    ax_int.set_zorder(3.0)
    tb_int = TextBox(ax_int, '', initial='1000',
                      color=PANEL, hovercolor=PANEL_EDGE)
    _style_textbox(tb_int)

    # Bottom row: [Clear A] [Clear B] [Clear All]    stats on right
    cl_y = send_card[1] + 0.018
    cl_w = 0.070
    btn_clear_a   = make_button(fig, (bx,                         cl_y, cl_w, 0.034),
                                 'Clear A',   kind='default')
    btn_clear_b   = make_button(fig, (bx + cl_w + 0.008,          cl_y, cl_w, 0.034),
                                 'Clear B',   kind='default')
    btn_clear_all = make_button(fig, (bx + 2 * (cl_w + 0.008),    cl_y, 0.075, 0.034),
                                 'Clear All', kind='default')
    btn_reset_stats = make_button(fig,
                                   (send_card[0] + send_card[2] - 0.105,
                                    cl_y, 0.090, 0.034),
                                   'Reset Stats', kind='default')
    for b in (btn_clear_a, btn_clear_b, btn_clear_all, btn_reset_stats):
        b._ui_ax.set_zorder(3.0)

    # ------------------------------------------------------------------
    # Log panels — one fig.text handle per card holding last N lines.
    # ------------------------------------------------------------------
    LOG_LINES = 28

    def _log_handle(card):
        return fig.text(card[0] + 0.010,
                         card[1] + card[3] - 0.022,
                         '', fontsize=8.0,
                         color=TEXT_DIM, family='monospace',
                         va='top', ha='left')

    log_h = {'a': _log_handle(log_a_card),
             'b': _log_handle(log_b_card)}
    log_buf = {'a': deque(maxlen=LOG_LINES), 'b': deque(maxlen=LOG_LINES)}

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------
    foot_left, foot_right = make_footer(fig, left='', right='')

    # ------------------------------------------------------------------
    # State + callbacks
    # ------------------------------------------------------------------
    ev_q = queue.Queue()
    worker = Worker(ev_q)

    state = {
        'seq':      0,
        'stats':    {'a_tx': 0, 'a_rx': 0, 'b_tx': 0, 'b_rx': 0},
        'auto':     False,
        'auto_next_t': 0.0,
        'log_dirty': {'a': False, 'b': False},
    }

    def _refresh_log(dev):
        state['log_dirty'][dev] = True

    def _flush_log_if_dirty():
        for dev in 'ab':
            if state['log_dirty'][dev]:
                log_h[dev].set_text('\n'.join(log_buf[dev]))
                state['log_dirty'][dev] = False

    def _log(dev, msg, tag='info'):
        ts = datetime.now().strftime('%H:%M:%S.') + \
              f'{datetime.now().microsecond // 1000:03d}'
        prefix = {'tx': 'TX', 'rx': 'RX', 'err': 'E ', 'info': '..'}.get(tag, '..')
        log_buf[dev].append(f'[{ts}] {prefix}  {msg}')
        _refresh_log(dev)

    def _set_port(dev, port):
        if worker.is_open(dev) and dev_meta[dev]['port'] != port:
            worker.disconnect(dev)
        dev_meta[dev]['port'] = port or ''
        label = (port or '(no ports)') + '  ▾'
        dev_meta[dev]['port_btn'].label.set_text(label)

    # --- port dropdown popup ------------------------------------------
    popup = {'dev': None, 'btns': []}

    def _close_popup():
        if popup['dev'] is None:
            return
        for b in popup['btns']:
            try:
                b._ui_ax.remove()
            except Exception:
                pass
        popup['btns'] = []
        popup['dev'] = None
        fig.canvas.draw_idle()

    def _open_popup(dev):
        if popup['dev'] == dev:
            _close_popup(); return
        _close_popup()
        ports = list_data_ports()
        if not ports:
            _log(dev, 'no USB ports detected', 'err'); return
        bbox = dev_meta[dev]['port_btn']._ui_ax.get_position()
        x, y, w, h = bbox.x0, bbox.y0, bbox.width, bbox.height
        btns = []
        for i, p in enumerate(ports):
            py = y - h * (i + 1)
            b = make_button(fig, (x, py, w, h), p, kind='default')
            b._ui_ax.set_zorder(20.0)
            b.label.set_fontsize(8.5)
            b.on_clicked(lambda _e, dv=dev, pp=p:
                         (_set_port(dv, pp), _close_popup()))
            btns.append(b)
        popup['dev'] = dev
        popup['btns'] = btns
        fig.canvas.draw_idle()

    def _refresh_ports(_evt=None):
        ports = list_data_ports()
        for dev in 'ab':
            if dev_meta[dev]['port'] and dev_meta[dev]['port'] not in ports:
                _set_port(dev, '')
        if ports and not dev_meta['a']['port']:
            _set_port('a', ports[0])
        if len(ports) > 1 and not dev_meta['b']['port']:
            for p in ports:
                if p != dev_meta['a']['port']:
                    _set_port('b', p); break
        for dev in 'ab':
            if not dev_meta[dev]['port']:
                dev_meta[dev]['port_btn'].label.set_text('(no ports)')
        _log('a', 'ports: ' + (', '.join(ports) if ports else '(none)'),
             'info')

    def _cycle_port(dev):
        _open_popup(dev)

    def _toggle(dev):
        if worker.is_open(dev):
            worker.disconnect(dev); return
        port = dev_meta[dev]['port']
        if not port:
            _log(dev, 'no port selected', 'err'); return
        other = 'b' if dev == 'a' else 'a'
        if worker.is_open(other) and dev_meta[other]['port'] == port:
            _log(dev, 'same port as other device', 'err'); return
        worker.connect(dev, port)

    def _auto_connect():
        ports = list_data_ports()
        if len(ports) != 2:
            return
        for dev in 'ab':
            if dev_meta[dev]['port'] and not worker.is_open(dev):
                worker.connect(dev, dev_meta[dev]['port'])

    def _send(from_dev):
        if not worker.is_open(from_dev):
            _log(from_dev, 'not connected — click Connect first', 'err')
            return
        state['seq'] += 1
        seq = state['seq']
        msg = tb_msg.text
        payload = struct.pack('<II', seq,
                              int(time.time() * 1000) & 0xFFFFFFFF) \
                  + msg.encode('utf-8')
        pkt = build_db_packet(DB_ID_TEST, 0x00, payload)
        n = worker.write(from_dev, pkt)
        if n:
            tgt = 'B' if from_dev == 'a' else 'A'
            _log(from_dev, f'-> {tgt} seq={seq} "{msg}"', 'tx')
            _log(from_dev, f'   {hex_fmt(pkt)}', 'tx')
            state['stats'][f'{from_dev}_tx'] += 1

    def _toggle_auto(_evt):
        state['auto'] = not state['auto']
        restyle_button(btn_auto,
                       'primary' if state['auto'] else 'default',
                       label=f'Auto: {"ON" if state["auto"] else "OFF"}')
        if state['auto']:
            state['auto_next_t'] = time.time()

    def _clear_log(dev):
        log_buf[dev].clear(); _refresh_log(dev)

    def _reset_stats(_evt=None):
        state['stats'] = {k: 0 for k in state['stats']}
        state['seq'] = 0

    # Wire up
    btn_refresh.on_clicked(_refresh_ports)
    dev_meta['a']['btn'].on_clicked(lambda _e: _toggle('a'))
    dev_meta['b']['btn'].on_clicked(lambda _e: _toggle('b'))
    dev_meta['a']['port_btn'].on_clicked(lambda _e: _cycle_port('a'))
    dev_meta['b']['port_btn'].on_clicked(lambda _e: _cycle_port('b'))
    btn_sa.on_clicked(lambda _e: _send('a'))
    btn_sb.on_clicked(lambda _e: _send('b'))
    btn_auto.on_clicked(_toggle_auto)
    btn_clear_a.on_clicked(lambda _e: _clear_log('a'))
    btn_clear_b.on_clicked(lambda _e: _clear_log('b'))
    btn_clear_all.on_clicked(lambda _e: (_clear_log('a'), _clear_log('b')))
    btn_reset_stats.on_clicked(_reset_stats)

    # ------------------------------------------------------------------
    # Event handler
    # ------------------------------------------------------------------
    def _handle(ev):
        kind = ev[0]
        if kind == 'conn':
            _, dev, ok, port, baud = ev
            meta = dev_meta[dev]
            if ok:
                restyle_button(meta['btn'], 'danger', label='Disconnect')
                meta['dot'].set_color(GOOD)
                _log(dev, f'connected: {port} @ {baud}', 'info')
            else:
                restyle_button(meta['btn'], 'primary', label='Connect')
                meta['dot'].set_color(TEXT_FAINT)
                _log(dev, 'disconnected', 'info')
        elif kind == 'log':
            _, dev, msg, tag = ev
            _log(dev, msg, tag)
        elif kind == 'rx':
            _, dev, raw = ev
            parsed = verify_db_packet(raw)
            if not parsed:
                _log(dev, f'bad checksum: {hex_fmt(raw)}', 'err')
                return
            mid, sid, payload = parsed
            info = f'id=0x{mid:02X} sub=0x{sid:02X} len={len(payload)}'
            if mid == DB_ID_TEST and len(payload) >= 8:
                seq = struct.unpack('<I', payload[:4])[0]
                ts  = struct.unpack('<I', payload[4:8])[0]
                text = payload[8:].decode('utf-8', errors='replace')
                now_ms = int(time.time() * 1000) & 0xFFFFFFFF
                lat = now_ms - ts
                if lat < 0: lat += 0x100000000
                info = f'seq={seq} "{text}" latency={lat}ms'
            src = 'A' if dev == 'b' else 'B'
            _log(dev, f'<- {src} {info}', 'rx')
            _log(dev, f'   {hex_fmt(raw)}', 'rx')
            state['stats'][f'{dev}_rx'] += 1

    # ------------------------------------------------------------------
    # Animation pump
    # ------------------------------------------------------------------
    from matplotlib.animation import FuncAnimation

    def _tick(_frame):
        # Drain events.
        try:
            while True:
                _handle(ev_q.get_nowait())
        except queue.Empty:
            pass

        # Flush any pending log text updates once per tick.
        _flush_log_if_dirty()

        # Auto-send.
        if state['auto'] and time.time() >= state['auto_next_t']:
            sent = False
            if worker.is_open('a'): _send('a'); sent = True
            if worker.is_open('b'): _send('b'); sent = True
            try:
                ms = max(100, int(tb_int.text))
            except ValueError:
                ms = 1000
            state['auto_next_t'] = time.time() + ms / 1000.0
            if not sent:
                # Avoid hot-looping if no port is open.
                state['auto_next_t'] = time.time() + 0.5

        # Footer / stats.
        s = state['stats']
        a_ok = worker.is_open('a'); b_ok = worker.is_open('b')
        foot_left.set_text(
            f'AP {dev_meta["a"]["port"] or "-"} '
            f'[{"ON" if a_ok else "off"}]   '
            f'STA {dev_meta["b"]["port"] or "-"} '
            f'[{"ON" if b_ok else "off"}]')
        foot_right.set_text(
            f'A tx={s["a_tx"]} rx={s["a_rx"]}    '
            f'B tx={s["b_tx"]} rx={s["b_rx"]}    '
            f'seq={state["seq"]}    '
            f'auto={"ON" if state["auto"] else "off"}')
        foot_right.set_color(GOOD if state['auto'] else TEXT_DIM)
        return ()

    anim = FuncAnimation(fig, _tick, interval=100, blit=False,
                          cache_frame_data=False)
    fig._keepalive_anim = anim
    fig._keepalive_worker = worker
    fig._keepalive_textboxes = (tb_msg, tb_int)
    fig._keepalive_buttons = (btn_refresh, btn_sa, btn_sb, btn_auto,
                               btn_clear_a, btn_clear_b, btn_clear_all,
                               btn_reset_stats,
                               dev_meta['a']['btn'], dev_meta['b']['btn'],
                               dev_meta['a']['port_btn'],
                               dev_meta['b']['port_btn'])

    def _on_close(_evt):
        for d in 'ab':
            worker.disconnect(d)

    fig.canvas.mpl_connect('close_event', _on_close)

    _refresh_ports()
    _auto_connect()
    return fig


def main():
    build_dashboard()
    plt.show()


if __name__ == '__main__':
    main()
