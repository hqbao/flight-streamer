#!/usr/bin/env python3
"""
dblink Bandwidth Lab — interactive dashboard for end-to-end link
characterisation between two ESP32-S3 dblink boards (one AP, one STA).

Test modes:
  • Burst    — pump as fast as USB-CDC allows, report delivered kbps + loss.
  • Paced    — pump at a target kbps, report loss vs offered rate.
  • Latency  — single-marker round trip (USB→WiFi→USB).
  • Sweep    — step through a list of rates, find the loss-free knee.

Dependencies: pyserial, matplotlib, and a sibling checkout of the
flight-controller repository, which supplies the shared dashboard primitives
tools/pytools/_ui.py — see tools/_fc_pytools.py.
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

# Pick a GUI backend before pyplot import.
if not os.environ.get('MPLBACKEND'):
    matplotlib.use('MacOSX' if sys.platform == 'darwin' else 'TkAgg')
import matplotlib.pyplot as plt          # noqa: E402
from matplotlib.widgets import TextBox    # noqa: E402

import serial                             # noqa: E402
import serial.tools.list_ports            # noqa: E402

# The dashboard primitives live in the sibling flight-controller repository;
# _fc_pytools resolves that cross-repository path and, if it is absent or has
# moved, exits with the path it tried instead of a bare ModuleNotFoundError.
from _fc_pytools import add_flight_controller_pytools  # noqa: E402
add_flight_controller_pytools()
from _ui import (                         # noqa: E402
    apply_theme, make_figure, add_panel, style_axes, make_button,
    restyle_button, make_footer, screen_fit_figsize, _inset,
    enable_scroll_zoom, add_scroll_hint,
    PANEL, PANEL_EDGE, TEXT, TEXT_DIM, TEXT_FAINT,
    ACCENT, GOOD, WARN, BAD,
    TRACE_1, TRACE_2,
)


# =============================================================================
# Wire markers
# =============================================================================
BW_MAGIC  = b'BW'
BW_HDR    = 6      # 'B''W' + seq:u32 LE
PNG_MAGIC = b'PN'
PNG_TAIL  = b'G'

DEFAULT_BAUD = 115200


# =============================================================================
# Link worker — owns both serial ports, drains them on background threads.
# =============================================================================
class LinkWorker:
    def __init__(self, ev_queue):
        self.ev = ev_queue
        self.ser = {'a': None, 'b': None}
        self.lock = threading.Lock()
        self._rx_buf = {'a': bytearray(), 'b': bytearray()}
        self._rx_count = {'a': 0, 'b': 0}
        self._threads = {}

    def connect(self, dev, port, baud):
        try:
            s = serial.Serial(port, baud, timeout=0.05)
            try:
                s.reset_input_buffer(); s.reset_output_buffer()
            except Exception:
                pass
        except Exception as e:
            self.ev.put(('log', f'[{dev.upper()}] open failed: {e}', 'err'))
            return False
        self.ser[dev] = s
        t = threading.Thread(target=self._reader, args=(dev,), daemon=True)
        self._threads[dev] = t
        t.start()
        self.ev.put(('conn', dev, True, port, baud))
        return True

    def disconnect(self, dev):
        s = self.ser[dev]
        self.ser[dev] = None
        if s:
            try: s.close()
            except Exception: pass
        self.ev.put(('conn', dev, False, None, None))

    def is_open(self, dev):
        return self.ser[dev] is not None

    def _reader(self, dev):
        s = self.ser[dev]
        while s is self.ser[dev] and s is not None:
            try:
                data = s.read(4096)
            except Exception:
                break
            if not data:
                continue
            with self.lock:
                self._rx_buf[dev].extend(data)
                self._rx_count[dev] += len(data)
                # Cap so an idle test can't grow it unbounded.
                if len(self._rx_buf[dev]) > 1_000_000:
                    del self._rx_buf[dev][:500_000]
            s = self.ser[dev]

    def snapshot_rx_buf(self, dev):
        with self.lock:
            buf = bytes(self._rx_buf[dev])
            self._rx_buf[dev].clear()
        return buf

    def reset_rx(self, dev):
        with self.lock:
            self._rx_buf[dev].clear()
            self._rx_count[dev] = 0

    def tx(self, dev, payload):
        s = self.ser[dev]
        if s is None:
            return 0
        try:
            return s.write(payload) or len(payload)
        except Exception as e:
            self.ev.put(('log', f'[{dev.upper()}] tx failed: {e}', 'err'))
            return 0


# =============================================================================
# Test workers (run off the UI thread, emit events into a queue).
# =============================================================================
def _parse_bw_seqs(buf):
    """Return list of sequence numbers found in ``buf``."""
    seqs = []
    i = 0
    n = len(buf)
    while i + BW_HDR <= n:
        if buf[i] == 0x42 and buf[i+1] == 0x57:        # 'B' 'W'
            seqs.append(struct.unpack_from('<I', buf, i+2)[0])
            i += BW_HDR
        else:
            i += 1
    return seqs


class _BasePump:
    """Common burst-style accounting + drain. Subclasses define `_should_send`."""
    def __init__(self, worker, tx_dev, rx_dev, duration, chunk, ev,
                 sample_period=0.2):
        self.worker = worker; self.ev = ev
        self.tx_dev = tx_dev; self.rx_dev = rx_dev
        self.duration = duration; self.chunk = max(chunk, BW_HDR)
        self.sample_period = sample_period
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self): self.thread.start()
    def cancel(self): self.stop.set()

    def _pre_event(self): return ('burst', {})

    def _run(self):
        w = self.worker
        chunk = self.chunk
        w.reset_rx(self.rx_dev)
        pad = b'\xA5' * (chunk - BW_HDR)
        seq = 0; sent_bytes = 0
        last_seen = -1; gaps = 0; rx_seqs_total = 0
        t0 = time.time()
        next_sample = t0 + self.sample_period
        sample_tx_b = 0; sample_rx_b = 0

        mode_name, extra = self._pre_event()
        info = {'chunk': chunk, 'duration': self.duration}; info.update(extra)
        self.ev.put(('test_start', mode_name,
                     f'{self.tx_dev}->{self.rx_dev}', info))

        while not self.stop.is_set() and time.time() - t0 < self.duration:
            if not self._should_send(time.time(), t0, seq):
                time.sleep(0.001); continue
            payload = BW_MAGIC + struct.pack('<I', seq) + pad
            n = w.tx(self.tx_dev, payload)
            if n: sent_bytes += n
            seq += 1
            now = time.time()
            if now >= next_sample:
                buf = w.snapshot_rx_buf(self.rx_dev)
                if buf:
                    sample_rx_b += len(buf)
                    seqs = _parse_bw_seqs(buf)
                    rx_seqs_total += len(seqs)
                    for s in seqs:
                        if last_seen >= 0 and s != last_seen + 1:
                            gaps += max(0, s - last_seen - 1)
                        last_seen = s
                dt = self.sample_period
                tx_kbps = (sent_bytes - sample_tx_b) * 8 / 1000.0 / dt
                rx_kbps = sample_rx_b * 8 / 1000.0 / dt
                self.ev.put(('sample', now - t0, tx_kbps, rx_kbps, gaps, seq))
                sample_tx_b = sent_bytes
                sample_rx_b = 0
                next_sample += self.sample_period

        # Drain trailing bytes for 1.5 s.
        drain_until = time.time() + 1.5
        while time.time() < drain_until:
            buf = w.snapshot_rx_buf(self.rx_dev)
            if buf:
                seqs = _parse_bw_seqs(buf)
                rx_seqs_total += len(seqs)
                for s in seqs:
                    if last_seen >= 0 and s != last_seen + 1:
                        gaps += max(0, s - last_seen - 1)
                    last_seen = s
            else:
                time.sleep(0.05)

        elapsed = max(1e-6, time.time() - t0)
        tx_kbps = sent_bytes * 8 / 1000.0 / elapsed
        rx_kbps = rx_seqs_total * chunk * 8 / 1000.0 / elapsed
        loss = (gaps / max(1, seq)) * 100.0
        summary = {'mode': mode_name, 'dir': f'{self.tx_dev}->{self.rx_dev}',
                   'chunk': chunk, 'duration': elapsed,
                   'tx_bytes': sent_bytes, 'tx_kbps': tx_kbps,
                   'rx_chunks': rx_seqs_total, 'rx_kbps': rx_kbps,
                   'sent_chunks': seq, 'gaps': gaps, 'loss_pct': loss}
        summary.update(extra)
        self.ev.put(('test_done', summary))


class BurstTest(_BasePump):
    def _should_send(self, now, t0, seq):
        return True


class PacedTest(_BasePump):
    def __init__(self, worker, tx_dev, rx_dev, duration, chunk, rate_kbps,
                 ev, sample_period=0.2):
        super().__init__(worker, tx_dev, rx_dev, duration, chunk, ev,
                         sample_period)
        self.rate_kbps = max(0.1, float(rate_kbps))
        self._period = (self.chunk * 8) / (self.rate_kbps * 1000.0)
        self._next_t = None

    def _pre_event(self): return ('paced', {'rate_kbps': self.rate_kbps})

    def _should_send(self, now, t0, seq):
        if self._next_t is None:
            self._next_t = t0
        if now < self._next_t:
            return False
        self._next_t += self._period
        return True


class LatencyTest:
    def __init__(self, worker, tx_dev, rx_dev, count, ev, timeout=0.5):
        self.worker = worker; self.ev = ev
        self.tx_dev = tx_dev; self.rx_dev = rx_dev
        self.count = count; self.timeout = timeout
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self): self.thread.start()
    def cancel(self): self.stop.set()

    def _run(self):
        w = self.worker
        self.ev.put(('test_start', 'latency',
                     f'{self.tx_dev}->{self.rx_dev}', {'count': self.count}))
        w.reset_rx(self.rx_dev)
        time.sleep(0.1); w.reset_rx(self.rx_dev)
        samples = []
        for i in range(self.count):
            if self.stop.is_set(): break
            marker = PNG_MAGIC + struct.pack('<I', i) + PNG_TAIL
            t0 = time.time()
            w.tx(self.tx_dev, marker)
            seen = False
            acc = bytearray()
            deadline = t0 + self.timeout
            while time.time() < deadline:
                buf = w.snapshot_rx_buf(self.rx_dev)
                if buf:
                    acc.extend(buf)
                    if marker in acc:
                        dt_ms = (time.time() - t0) * 1000.0
                        samples.append(dt_ms)
                        self.ev.put(('ping', i, dt_ms))
                        seen = True; break
                else:
                    time.sleep(0.002)
            if not seen:
                self.ev.put(('ping', i, None))
            time.sleep(0.03)
        summary = {'mode': 'latency',
                   'dir': f'{self.tx_dev}->{self.rx_dev}',
                   'count': self.count, 'ok': len(samples),
                   'samples': samples}
        if samples:
            g = sorted(samples)
            summary.update(min=g[0], max=g[-1],
                           p50=g[len(g)//2],
                           p95=g[int(len(g)*0.95)],
                           avg=sum(g)/len(g))
        self.ev.put(('test_done', summary))


class SweepTest:
    def __init__(self, worker, tx_dev, rx_dev, chunk, duration, rates_kbps, ev):
        self.worker = worker; self.ev = ev
        self.tx_dev = tx_dev; self.rx_dev = rx_dev
        self.chunk = chunk; self.duration = duration
        self.rates = list(rates_kbps)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self._current = None

    def start(self): self.thread.start()
    def cancel(self):
        self.stop.set()
        if self._current: self._current.cancel()

    def _run(self):
        self.ev.put(('test_start', 'sweep',
                     f'{self.tx_dev}->{self.rx_dev}',
                     {'chunk': self.chunk, 'rates': self.rates,
                      'duration': self.duration}))
        results = []
        sub_q = queue.Queue()
        for r in self.rates:
            if self.stop.is_set(): break
            self._current = PacedTest(self.worker, self.tx_dev, self.rx_dev,
                                       self.duration, self.chunk, r, sub_q,
                                       sample_period=0.25)
            self._current.start()
            while True:
                try:
                    msg = sub_q.get(timeout=0.5)
                except queue.Empty:
                    if self.stop.is_set(): self._current.cancel()
                    continue
                if msg[0] == 'test_done':
                    s = msg[1]
                    results.append({'rate_kbps': r,
                                    'tx_kbps': s['tx_kbps'],
                                    'rx_kbps': s['rx_kbps'],
                                    'loss_pct': s['loss_pct']})
                    self.ev.put(('sweep_point', r, s['tx_kbps'],
                                 s['rx_kbps'], s['loss_pct']))
                    break
                self.ev.put(msg)        # forward live samples
            time.sleep(0.4)
        self.ev.put(('test_done', {'mode': 'sweep',
                                    'dir': f'{self.tx_dev}->{self.rx_dev}',
                                    'results': results}))


# =============================================================================
# Dashboard
# =============================================================================
def _style_textbox(tb):
    """Force a matplotlib TextBox into the dashboard palette."""
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


# Substring filters: ports whose device path matches any of these are
# excluded from the dropdown. ``debug-console`` is the macOS USB-CDC debug
# channel on the ESP32-S3 — never the WiFi data port.
_PORT_HIDE = ('debug-console', 'Bluetooth', 'wlan-debug', 'wireless')

# Substring filters that mark a port as a likely candidate (sorted first).
_PORT_PREFER = ('usbmodem', 'usbserial', 'SLAB_USB', 'ttyACM', 'ttyUSB')


def list_data_ports():
    """Return USB serial ports that look like a dblink data CDC, filtering
    out debug consoles and unrelated devices."""
    out = []
    for p in serial.tools.list_ports.comports():
        dev = p.device
        if any(h in dev for h in _PORT_HIDE):
            continue
        out.append(dev)
    # Prefer real USB CDC over /dev/cu.* extras; preserve original order
    # within each tier so AP/STA stay distinguishable.
    out.sort(key=lambda d: (0 if any(k in d for k in _PORT_PREFER) else 1,
                             d))
    return out


def build_dashboard():
    apply_theme()
    matplotlib.rcParams['toolbar'] = 'None'

    figsize = screen_fit_figsize(14.4, 8.6)
    fig, _content = make_figure(
        'DBLink Bandwidth Lab',
        subtitle='USB-CDC <-> WiFi/UDP throughput, loss and latency',
        size=figsize,
    )

    # ------------------------------------------------------------------
    # Layout (figure-relative rectangles).
    # ------------------------------------------------------------------
    LX, LW = 0.012, 0.345
    RX, RW = 0.365, 0.623

    dev_card    = (LX, 0.700, LW, 0.220)
    param_card  = (LX, 0.420, LW, 0.270)
    action_card = (LX, 0.200, LW, 0.210)
    log_card    = (LX, 0.040, LW, 0.150)

    live_card  = (RX, 0.540, RW, 0.380)
    sweep_card = (RX, 0.290, RW, 0.240)
    lat_card   = (RX, 0.040, RW, 0.240)

    add_panel(fig, dev_card,    title='DEVICES')
    add_panel(fig, param_card,  title='PARAMETERS')
    add_panel(fig, action_card, title='RUN')
    add_panel(fig, log_card,    title='LOG')
    add_panel(fig, live_card,   title='LIVE THROUGHPUT')
    add_panel(fig, sweep_card,  title='RATE SWEEP')
    add_panel(fig, lat_card,    title='LATENCY')

    # ------------------------------------------------------------------
    # DEVICES card — two rows (AP, STA) of
    #   [label] [port dropdown button (cycles)] [connect] [status dot]
    # The dropdown lists only real USB CDC ports (debug-console hidden).
    # ------------------------------------------------------------------
    dev_meta = {}  # dev -> dict(port_btn, port, btn, dot)

    def _make_device_row(dev, label, y_top):
        fig.text(dev_card[0] + 0.012, y_top - 0.018, label,
                 fontsize=9, fontweight='bold', color=TEXT,
                 family='monospace', va='top')
        port_x = dev_card[0] + 0.060
        port_w = LW - 0.060 - 0.110
        port_y = y_top - 0.045
        port_h = 0.034
        port_btn = make_button(fig, (port_x, port_y, port_w, port_h),
                                '(no ports)', kind='default')
        port_btn._ui_ax.set_zorder(3.0)
        port_btn.label.set_fontsize(8.5)
        # connect button
        btn_x = port_x + port_w + 0.006
        btn_w = LW - (btn_x - dev_card[0]) - 0.040
        btn = make_button(fig, (btn_x, port_y, btn_w, port_h),
                          'Connect', kind='primary')
        btn._ui_ax.set_zorder(3.0)
        # status dot
        dot_x = (btn_x + btn_w + 0.012 - dev_card[0]) / LW
        dot_y = (port_y + port_h * 0.5 - dev_card[1]) / dev_card[3]
        dot = fig.text(dev_card[0] + dot_x * LW,
                        dev_card[1] + dot_y * dev_card[3],
                        '\u25CF', fontsize=12, color=TEXT_FAINT,
                        ha='center', va='center')
        dev_meta[dev] = {'port_btn': port_btn, 'port': '',
                          'btn': btn, 'dot': dot}

    _make_device_row('a', 'AP',  dev_card[1] + dev_card[3] - 0.020)
    _make_device_row('b', 'STA', dev_card[1] + dev_card[3] - 0.090)

    # Bottom row: Refresh + Baud
    bot_y = dev_card[1] + 0.012
    bot_h = 0.034
    btn_refresh = make_button(fig, (dev_card[0] + 0.012, bot_y, 0.075, bot_h),
                              'Refresh', kind='default')
    btn_refresh._ui_ax.set_zorder(3.0)
    # Baud label + textbox
    fig.text(dev_card[0] + 0.110, bot_y + bot_h / 2,
             'BAUD', fontsize=8.5, color=TEXT_DIM, family='monospace',
             ha='left', va='center')
    ax_baud = fig.add_axes((dev_card[0] + 0.155, bot_y, 0.075, bot_h))
    ax_baud.set_zorder(3.0)
    tb_baud = TextBox(ax_baud, '', initial=str(DEFAULT_BAUD),
                      color=PANEL, hovercolor=PANEL_EDGE)
    _style_textbox(tb_baud)

    # ------------------------------------------------------------------
    # PARAMETERS card — labeled text-boxes.
    # ------------------------------------------------------------------
    pe_x = param_card[0] + 0.012
    pe_w = 0.085
    pe_h = 0.032

    def _param_row(y_top, items):
        """items = [(label, init_value, key)] up to 2 per row."""
        row = []
        for idx, (label, init, _key) in enumerate(items):
            base_x = pe_x + idx * (LW * 0.49)
            fig.text(base_x, y_top, label,
                     fontsize=8.5, color=TEXT_DIM, family='monospace',
                     va='top')
            ax = fig.add_axes((base_x, y_top - 0.045, pe_w, pe_h))
            ax.set_zorder(3.0)
            tb = TextBox(ax, '', initial=init,
                         color=PANEL, hovercolor=PANEL_EDGE)
            _style_textbox(tb)
            row.append(tb)
        return row

    y0 = param_card[1] + param_card[3] - 0.018
    # Direction is a 2-button toggle (AP->STA, STA->AP), not a textbox.
    fig.text(pe_x, y0, 'DIRECTION', fontsize=8.5, color=TEXT_DIM,
             family='monospace', va='top')
    btn_dir_ab = make_button(fig, (pe_x, y0 - 0.045, 0.085, pe_h),
                              'AP -> STA', kind='primary')
    btn_dir_ba = make_button(fig, (pe_x + 0.090, y0 - 0.045, 0.085, pe_h),
                              'STA -> AP', kind='default')
    btn_dir_ab._ui_ax.set_zorder(3.0)
    btn_dir_ba._ui_ax.set_zorder(3.0)

    # Chunk size, Duration
    yA = y0 - 0.085
    fig.text(pe_x, yA, 'CHUNK B', fontsize=8.5, color=TEXT_DIM,
             family='monospace', va='top')
    ax_chunk = fig.add_axes((pe_x, yA - 0.045, 0.085, pe_h))
    ax_chunk.set_zorder(3.0)
    tb_chunk = TextBox(ax_chunk, '', initial='256',
                       color=PANEL, hovercolor=PANEL_EDGE)
    _style_textbox(tb_chunk)
    fig.text(pe_x + 0.095, yA, 'DURATION s', fontsize=8.5, color=TEXT_DIM,
             family='monospace', va='top')
    ax_dur = fig.add_axes((pe_x + 0.095, yA - 0.045, 0.085, pe_h))
    ax_dur.set_zorder(3.0)
    tb_dur = TextBox(ax_dur, '', initial='8',
                     color=PANEL, hovercolor=PANEL_EDGE)
    _style_textbox(tb_dur)

    # Paced rate + ping count
    yB = yA - 0.085
    fig.text(pe_x, yB, 'PACED kbps', fontsize=8.5, color=TEXT_DIM,
             family='monospace', va='top')
    ax_rate = fig.add_axes((pe_x, yB - 0.045, 0.085, pe_h))
    ax_rate.set_zorder(3.0)
    tb_rate = TextBox(ax_rate, '', initial='100',
                      color=PANEL, hovercolor=PANEL_EDGE)
    _style_textbox(tb_rate)
    fig.text(pe_x + 0.095, yB, 'PING N', fontsize=8.5, color=TEXT_DIM,
             family='monospace', va='top')
    ax_ping = fig.add_axes((pe_x + 0.095, yB - 0.045, 0.060, pe_h))
    ax_ping.set_zorder(3.0)
    tb_ping = TextBox(ax_ping, '', initial='20',
                      color=PANEL, hovercolor=PANEL_EDGE)
    _style_textbox(tb_ping)

    # Sweep rates (wider box)
    yC = yB - 0.085
    fig.text(pe_x, yC, 'SWEEP kbps (comma list)',
             fontsize=8.5, color=TEXT_DIM,
             family='monospace', va='top')
    ax_sweep = fig.add_axes((pe_x, yC - 0.045, LW - 0.024, pe_h))
    ax_sweep.set_zorder(3.0)
    tb_sweep = TextBox(ax_sweep, '',
                       initial='16,32,64,96,128,160,200,300,500',
                       color=PANEL, hovercolor=PANEL_EDGE)
    _style_textbox(tb_sweep)

    # ------------------------------------------------------------------
    # ACTION card — Burst / Paced / Latency / Sweep + Stop + Clear.
    # ------------------------------------------------------------------
    act_x = action_card[0] + 0.012
    act_w = (LW - 0.024 - 0.018) / 2.0   # two columns
    act_h = 0.040
    row1_y = action_card[1] + action_card[3] - act_h - 0.030
    row2_y = row1_y - (act_h + 0.012)
    row3_y = row2_y - (act_h + 0.012)

    btn_burst   = make_button(fig, (act_x,                       row1_y, act_w, act_h),
                               'Burst',   kind='primary')
    btn_paced   = make_button(fig, (act_x + act_w + 0.018,       row1_y, act_w, act_h),
                               'Paced',   kind='primary')
    btn_latency = make_button(fig, (act_x,                       row2_y, act_w, act_h),
                               'Latency', kind='primary')
    btn_sweep   = make_button(fig, (act_x + act_w + 0.018,       row2_y, act_w, act_h),
                               'Sweep',   kind='primary')
    btn_stop    = make_button(fig, (act_x,                       row3_y, act_w, act_h),
                               'Stop',    kind='danger')
    btn_clear   = make_button(fig, (act_x + act_w + 0.018,       row3_y, act_w, act_h),
                               'Clear',   kind='default')
    for _b in (btn_burst, btn_paced, btn_latency, btn_sweep,
               btn_stop, btn_clear):
        _b._ui_ax.set_zorder(3.0)

    # ------------------------------------------------------------------
    # LOG card — last N lines as a single monospace text block.
    # ------------------------------------------------------------------
    log_x = log_card[0] + 0.010
    log_y_top = log_card[1] + log_card[3] - 0.022
    log_h = fig.text(log_x, log_y_top, '',
                      fontsize=8.0, color=TEXT_DIM, family='monospace',
                      va='top', ha='left')

    # ------------------------------------------------------------------
    # PLOTS
    # ------------------------------------------------------------------
    ax_live  = fig.add_axes(_inset(live_card, left=0.06, right=0.02,
                                    top=0.14, bottom=0.18), zorder=2.0)
    style_axes(ax_live, xlabel='t [s]', ylabel='kbps')
    line_tx, = ax_live.plot([], [], color=TRACE_1, lw=1.4,
                             label='offered (USB tx)')
    line_rx, = ax_live.plot([], [], color=GOOD, lw=1.6,
                             label='delivered (USB rx)')
    ax_live.legend(loc='upper right', fontsize=8, framealpha=0.9)
    ax_live.set_xlim(0, 10); ax_live.set_ylim(0, 200)
    enable_scroll_zoom(ax_live)

    ax_sweep_plot = fig.add_axes(_inset(sweep_card, left=0.08, right=0.10,
                                         top=0.14, bottom=0.20), zorder=2.0)
    style_axes(ax_sweep_plot, xlabel='offered kbps', ylabel='delivered kbps')
    line_sweep_rx, = ax_sweep_plot.plot([], [], 'o-',
                                          color=GOOD, lw=1.6,
                                          label='delivered')
    ax_sweep_plot.yaxis.label.set_color(GOOD)
    ax_sweep_loss = ax_sweep_plot.twinx()
    ax_sweep_loss.set_facecolor((0, 0, 0, 0))
    for sp in ax_sweep_loss.spines.values():
        sp.set_color(PANEL_EDGE); sp.set_linewidth(0.8)
    ax_sweep_loss.tick_params(colors=TEXT_DIM, labelsize=8)
    ax_sweep_loss.set_ylabel('loss %', color=BAD, fontsize=9)
    ax_sweep_loss.set_ylim(-2, 105)
    line_sweep_loss, = ax_sweep_loss.plot([], [], 's--',
                                            color=BAD, lw=1.3,
                                            label='loss %')
    enable_scroll_zoom(ax_sweep_plot)

    ax_lat = fig.add_axes(_inset(lat_card, left=0.06, right=0.02,
                                  top=0.14, bottom=0.20), zorder=2.0)
    style_axes(ax_lat, xlabel='sample #', ylabel='RTT [ms]')
    line_lat, = ax_lat.plot([], [], 'o-', color=TRACE_2, lw=1.2)
    ax_lat.set_xlim(0, 20); ax_lat.set_ylim(0, 100)
    enable_scroll_zoom(ax_lat)

    add_scroll_hint(fig)

    # ------------------------------------------------------------------
    # Footer.
    # ------------------------------------------------------------------
    foot_left, foot_right = make_footer(fig, left='', right='')

    # ------------------------------------------------------------------
    # State + callbacks
    # ------------------------------------------------------------------
    ev_q = queue.Queue()
    worker = LinkWorker(ev_q)

    state = {
        'test':         None,
        'direction':    'a->b',   # 'a->b' or 'b->a'
        'port_list':    [],
        't_series':     deque(maxlen=600),
        'tx_series':    deque(maxlen=600),
        'rx_series':    deque(maxlen=600),
        'sweep_x':      [],
        'sweep_rx':     [],
        'sweep_loss':   [],
        'lat_samples':  [],
        'log_lines':    deque(maxlen=8),
        'summary':      '',
        'summary_col':  TEXT_DIM,
        'live_t0':      0.0,
        'live_tx':      0.0,
        'live_rx':      0.0,
        'live_loss':    0.0,
    }

    def _refresh_log_text():
        log_h.set_text('\n'.join(state['log_lines']))

    def _log(msg, tag='info'):
        ts = datetime.now().strftime('%H:%M:%S')
        color_map = {'err': BAD, 'ok': GOOD, 'hdr': ACCENT, 'info': TEXT_DIM}
        tag_letter = {'err': 'E', 'ok': 'OK', 'hdr': '>>', 'info': '..'}
        # We keep a single colour for the whole block (TEXT_DIM); use prefix
        # tokens to flag severity since matplotlib text doesn't span colours.
        prefix = tag_letter.get(tag, '..')
        state['log_lines'].append(f'[{ts}] {prefix}  {msg}')
        _refresh_log_text()
        # Briefly flash the summary line for non-info messages.
        if tag in ('err', 'ok', 'hdr'):
            state['summary'] = msg
            state['summary_col'] = color_map[tag]

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
        # Toggle: clicking the same row closes the popup.
        if popup['dev'] == dev:
            _close_popup(); return
        _close_popup()
        ports = list_data_ports()
        state['port_list'] = ports
        if not ports:
            _log(f'{dev.upper()}: no USB ports detected', 'err'); return
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

    def _refresh_ports():
        ports = list_data_ports()
        state['port_list'] = ports
        for dev in 'ab':
            cur = dev_meta[dev]['port']
            if cur and cur not in ports:
                _set_port(dev, '')
        # Autofill empty selections.
        if ports and not dev_meta['a']['port']:
            _set_port('a', ports[0])
        if len(ports) > 1 and not dev_meta['b']['port']:
            # Pick the first port that isn't already on AP.
            for p in ports:
                if p != dev_meta['a']['port']:
                    _set_port('b', p); break
        # If still empty, mirror Refresh button to show '(no ports)'.
        for dev in 'ab':
            if not dev_meta[dev]['port']:
                dev_meta[dev]['port_btn'].label.set_text('(no ports)')
        _log('Ports: ' + (', '.join(ports) if ports else '(none)'), 'info')

    def _auto_connect():
        """If exactly two USB ports are detected, connect both immediately."""
        ports = list_data_ports()
        if len(ports) != 2:
            _log(f'auto-connect: need exactly 2 ports, found {len(ports)}',
                 'info')
            return
        if dev_meta['a']['port'] and not worker.is_open('a'):
            try:
                baud = int(tb_baud.text.strip())
            except ValueError:
                baud = DEFAULT_BAUD
            worker.connect('a', dev_meta['a']['port'], baud)
        if dev_meta['b']['port'] and not worker.is_open('b'):
            try:
                baud = int(tb_baud.text.strip())
            except ValueError:
                baud = DEFAULT_BAUD
            worker.connect('b', dev_meta['b']['port'], baud)

    def _cycle_port(dev):
        _open_popup(dev)

    def _toggle_connect(dev):
        if worker.is_open(dev):
            worker.disconnect(dev); return
        port = dev_meta[dev]['port']
        if not port:
            _log(f'{dev.upper()}: no port selected', 'err'); return
        other = 'b' if dev == 'a' else 'a'
        if worker.is_open(other) and dev_meta[other]['port'] == port:
            _log(f'{dev.upper()}: same port as other device', 'err'); return
        try:
            baud = int(tb_baud.text.strip())
        except ValueError:
            baud = DEFAULT_BAUD
        worker.connect(dev, port, baud)

    def _set_direction(d):
        state['direction'] = d
        restyle_button(btn_dir_ab, 'primary' if d == 'a->b' else 'default')
        restyle_button(btn_dir_ba, 'primary' if d == 'b->a' else 'default')

    def _running(running):
        for b in (btn_burst, btn_paced, btn_latency, btn_sweep):
            b.label.set_color(TEXT_FAINT if running else TEXT)
            b._running_disabled = running

    def _start(mode):
        if not (worker.is_open('a') and worker.is_open('b')):
            _log('Connect both AP and STA first', 'err')
            state['summary'] = 'Connect both AP and STA first'
            state['summary_col'] = BAD
            return
        if state['test'] is not None:
            _log('A test is already running', 'err'); return
        tx, rx = ('a', 'b') if state['direction'] == 'a->b' else ('b', 'a')
        try:
            chunk = int(tb_chunk.text)
            duration = float(tb_dur.text)
        except ValueError:
            _log('Bad numeric parameter (chunk / duration)', 'err'); return

        _clear_live()
        if mode != 'sweep':
            _clear_sweep()
        _log(f'=== {mode.upper()}  {tx.upper()}->{rx.upper()}  '
              f'chunk={chunk}B  dur={duration}s ===', 'hdr')

        if mode == 'burst':
            t = BurstTest(worker, tx, rx, duration, chunk, ev_q)
        elif mode == 'paced':
            try: rate = float(tb_rate.text)
            except ValueError:
                _log('Bad paced rate', 'err'); return
            t = PacedTest(worker, tx, rx, duration, chunk, rate, ev_q)
        elif mode == 'latency':
            try: count = int(tb_ping.text)
            except ValueError:
                _log('Bad ping count', 'err'); return
            state['lat_samples'].clear()
            line_lat.set_data([], [])
            t = LatencyTest(worker, tx, rx, count, ev_q)
        elif mode == 'sweep':
            try:
                rates = [float(x) for x in tb_sweep.text.split(',') if x.strip()]
            except ValueError:
                _log('Bad sweep rates', 'err'); return
            t = SweepTest(worker, tx, rx, chunk, duration, rates, ev_q)
        else:
            return
        state['test'] = t
        _running(True)
        t.start()

    def _stop():
        if state['test']:
            state['test'].cancel()
            _log('Stop requested', 'info')

    def _clear_live():
        state['t_series'].clear()
        state['tx_series'].clear()
        state['rx_series'].clear()
        line_tx.set_data([], [])
        line_rx.set_data([], [])

    def _clear_sweep():
        state['sweep_x'].clear()
        state['sweep_rx'].clear()
        state['sweep_loss'].clear()
        line_sweep_rx.set_data([], [])
        line_sweep_loss.set_data([], [])

    def _clear_all(_evt=None):
        _clear_live(); _clear_sweep()
        state['lat_samples'].clear()
        line_lat.set_data([], [])
        ax_live.set_xlim(0, 10); ax_live.set_ylim(0, 200)
        ax_lat.set_xlim(0, 20); ax_lat.set_ylim(0, 100)
        ax_sweep_plot.set_xlim(0, 500); ax_sweep_plot.set_ylim(0, 500)
        ax_sweep_loss.set_ylim(-2, 105)
        fig.canvas.draw_idle()

    # Wire buttons
    btn_refresh.on_clicked(lambda _e: _refresh_ports())
    dev_meta['a']['btn'].on_clicked(lambda _e: _toggle_connect('a'))
    dev_meta['b']['btn'].on_clicked(lambda _e: _toggle_connect('b'))
    dev_meta['a']['port_btn'].on_clicked(lambda _e: _cycle_port('a'))
    dev_meta['b']['port_btn'].on_clicked(lambda _e: _cycle_port('b'))
    btn_dir_ab.on_clicked(lambda _e: _set_direction('a->b'))
    btn_dir_ba.on_clicked(lambda _e: _set_direction('b->a'))

    def _guarded(mode):
        def _cb(_e):
            if state['test'] is not None:
                _log('Already running — press Stop first', 'err'); return
            _start(mode)
        return _cb

    btn_burst.on_clicked(_guarded('burst'))
    btn_paced.on_clicked(_guarded('paced'))
    btn_latency.on_clicked(_guarded('latency'))
    btn_sweep.on_clicked(_guarded('sweep'))
    btn_stop.on_clicked(lambda _e: _stop())
    btn_clear.on_clicked(_clear_all)

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
                _log(f"{dev.upper()} connected: {port} @ {baud}", 'ok')
            else:
                restyle_button(meta['btn'], 'primary', label='Connect')
                meta['dot'].set_color(TEXT_FAINT)
                _log(f"{dev.upper()} disconnected", 'info')
        elif kind == 'log':
            _, msg, tag = ev; _log(msg, tag)
        elif kind == 'test_start':
            _, mode, direction, info = ev
            _log(f'{mode} {direction} {info}', 'info')
        elif kind == 'sample':
            _, t, tx_k, rx_k, gaps, sent = ev
            state['t_series'].append(t)
            state['tx_series'].append(tx_k)
            state['rx_series'].append(rx_k)
            line_tx.set_data(list(state['t_series']),
                              list(state['tx_series']))
            line_rx.set_data(list(state['t_series']),
                              list(state['rx_series']))
            if not getattr(ax_live, '_user_zoom', False):
                ax_live.set_xlim(0, max(10.0, t * 1.05))
                ymax = max(max(state['tx_series'] or [1]),
                            max(state['rx_series'] or [1])) * 1.15
                ax_live.set_ylim(0, max(50.0, ymax))
            state['live_t0'] = t
            state['live_tx'] = tx_k
            state['live_rx'] = rx_k
            state['live_loss'] = (gaps / max(1, sent)) * 100.0
        elif kind == 'ping':
            _, idx, dt_ms = ev
            state['lat_samples'].append(dt_ms if dt_ms is not None
                                          else float('nan'))
            xs = list(range(len(state['lat_samples'])))
            ys = list(state['lat_samples'])
            line_lat.set_data(xs, ys)
            if not getattr(ax_lat, '_user_zoom', False):
                ax_lat.set_xlim(0, max(20, len(xs) + 1))
                ok = [v for v in ys if v == v]   # NaN-safe
                if ok:
                    ax_lat.set_ylim(0, max(100, max(ok) * 1.2))
        elif kind == 'sweep_point':
            _, rate, tx_k, rx_k, loss = ev
            state['sweep_x'].append(rate)
            state['sweep_rx'].append(rx_k)
            state['sweep_loss'].append(loss)
            line_sweep_rx.set_data(state['sweep_x'], state['sweep_rx'])
            line_sweep_loss.set_data(state['sweep_x'], state['sweep_loss'])
            if not getattr(ax_sweep_plot, '_user_zoom', False):
                xmax = max(state['sweep_x']) * 1.1
                ymax = max(state['sweep_rx']) * 1.2 + 1.0
                ax_sweep_plot.set_xlim(0, max(xmax, 50))
                ax_sweep_plot.set_ylim(0, max(ymax, 50))
            _log(f'  sweep {rate:5.0f} kbps  ->  rx {rx_k:6.1f} kbps  '
                  f'loss {loss:5.2f}%', 'info')
        elif kind == 'test_done':
            summary = ev[1]
            state['test'] = None
            _running(False)
            _render_summary(summary)

    def _render_summary(s):
        mode = s.get('mode', '?')
        if mode == 'latency':
            if s.get('ok', 0) == 0:
                txt = 'latency: ALL LOST'; col = BAD
            else:
                txt = (f"latency  ok={s['ok']}/{s['count']}  "
                       f"min={s['min']:.1f} p50={s['p50']:.1f} "
                       f"avg={s['avg']:.1f} p95={s['p95']:.1f} "
                       f"max={s['max']:.1f} ms")
                col = GOOD
        elif mode == 'sweep':
            results = s.get('results', [])
            if not results:
                txt = 'sweep: no results'; col = WARN
            else:
                clean = [r for r in results if r['loss_pct'] < 1.0]
                knee = (clean[-1]['rate_kbps'], clean[-1]['rx_kbps']) \
                        if clean else (0, 0)
                peak = max(results, key=lambda r: r['rx_kbps'])
                txt = (f"sweep  knee={knee[0]:.0f} kbps "
                       f"(rx {knee[1]:.0f})  "
                       f"peak rx={peak['rx_kbps']:.0f} @ "
                       f"offered {peak['rate_kbps']:.0f}")
                col = GOOD if clean else WARN
        else:
            loss = s.get('loss_pct', 0)
            col = GOOD if loss <= 5 else (WARN if loss <= 20 else BAD)
            txt = (f"{mode}  sent={s['sent_chunks']}  "
                   f"tx={s['tx_kbps']:.1f} kbps  "
                   f"rx={s['rx_kbps']:.1f} kbps  "
                   f"gaps={s['gaps']}  loss={s['loss_pct']:.2f}%")
        state['summary'] = txt
        state['summary_col'] = col
        _log(txt, 'ok' if col == GOOD else ('hdr' if col == WARN else 'err'))

    # ------------------------------------------------------------------
    # Animation pump
    # ------------------------------------------------------------------
    from matplotlib.animation import FuncAnimation

    def _tick(_frame):
        # Drain events
        try:
            while True:
                _handle(ev_q.get_nowait())
        except queue.Empty:
            pass

        # Footer.
        running = (state['test'] is not None)
        a_ok = worker.is_open('a'); b_ok = worker.is_open('b')
        left = (f'AP {dev_meta["a"]["port"] or "-"} '
                f'[{"ON" if a_ok else "off"}]   '
                f'STA {dev_meta["b"]["port"] or "-"} '
                f'[{"ON" if b_ok else "off"}]')
        foot_left.set_text(left)
        if running:
            right = (f'live tx={state["live_tx"]:6.1f} kbps  '
                     f'rx={state["live_rx"]:6.1f} kbps  '
                     f'loss={state["live_loss"]:5.2f}%')
        elif state['summary']:
            right = state['summary']
        else:
            right = 'idle'
        foot_right.set_text(right)
        foot_right.set_color(state['summary_col'] if not running else TEXT_DIM)
        return ()

    anim = FuncAnimation(fig, _tick, interval=100, blit=False,
                          cache_frame_data=False)
    fig._keepalive_anim = anim
    fig._keepalive_worker = worker
    fig._keepalive_textboxes = (tb_baud, tb_chunk, tb_dur, tb_rate, tb_ping,
                                 tb_sweep)
    fig._keepalive_buttons = (btn_burst, btn_paced, btn_latency, btn_sweep,
                               btn_stop, btn_clear, btn_refresh,
                               btn_dir_ab, btn_dir_ba,
                               dev_meta['a']['btn'], dev_meta['b']['btn'],
                               dev_meta['a']['port_btn'],
                               dev_meta['b']['port_btn'])

    def _on_close(_evt):
        for d in 'ab':
            worker.disconnect(d)

    fig.canvas.mpl_connect('close_event', _on_close)

    # Boot: autofill ports.
    _refresh_ports()
    # One-shot: if exactly 2 USB ports are present, connect both so a
    # single click on Burst / Paced / Sweep just works.
    _auto_connect()

    return fig


def main():
    build_dashboard()
    plt.show()


if __name__ == '__main__':
    main()
