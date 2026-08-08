#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Capture binary CSI frames from csi_recv and save legacy ASCII CSV/log files."""

import argparse
import csv
import json
import struct
import sys
import time
from pathlib import Path

import serial

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from proto.csi_binary_proto import (
    CSI_BINARY_C5C6_FRAME_SIZE,
    CSI_BINARY_C5C6_PAYLOAD,
    CSI_BINARY_HEADER,
    CSI_BINARY_LAYOUT_C5C6,
    CSI_BINARY_LAYOUT_LEGACY,
    CSI_BINARY_LEGACY_FRAME_SIZE,
    CSI_BINARY_LEGACY_PAYLOAD,
    CSI_BINARY_MAGIC,
    CSI_BINARY_MAX_LEN,
    CSI_BINARY_VERSION,
    DATA_COLUMNS_NAMES,
    DATA_COLUMNS_NAMES_C5C6,
    crc16_xmodem,
)

EXPECTED_LEN_HT40 = 384
EXPECTED_LEN_HT20 = 128


def _mac_str(mac_bytes):
    return ':'.join('%02x' % b for b in mac_bytes)


def _csi_json(csi_values, length):
    count = min(int(length), len(csi_values), CSI_BINARY_MAX_LEN)
    return json.dumps([int(csi_values[i]) for i in range(count)])


def _row_from_payload(to_row, payload):
    try:
        return to_row(payload), None
    except (struct.error, IndexError, ValueError, OverflowError) as exc:
        return None, str(exc)


def legacy_payload_to_row(payload):
    unpacked = CSI_BINARY_LEGACY_PAYLOAD.unpack(payload)
    idx = 0
    pkt_id = unpacked[idx]
    idx += 1
    mac = unpacked[idx]
    idx += 1
    rssi = unpacked[idx]
    idx += 1
    rate = unpacked[idx]
    idx += 1
    sig_mode, mcs, bandwidth, smoothing, not_sounding = unpacked[idx:idx + 5]
    idx += 5
    aggregation, stbc, fec_coding, sgi = unpacked[idx:idx + 4]
    idx += 4
    noise_floor = unpacked[idx]
    idx += 1
    ampdu_cnt, channel, secondary_channel = unpacked[idx:idx + 3]
    idx += 3
    local_timestamp = unpacked[idx]
    idx += 1
    ant, sig_len, rx_format = unpacked[idx:idx + 3]
    idx += 3
    length = unpacked[idx]
    idx += 1
    first_word_invalid = unpacked[idx]
    idx += 2
    csi_values = unpacked[idx:idx + CSI_BINARY_MAX_LEN]
    effective_len = min(int(length), len(csi_values), CSI_BINARY_MAX_LEN)

    return [
        'CSI_DATA',
        str(pkt_id),
        _mac_str(mac),
        str(rssi),
        str(rate),
        str(sig_mode),
        str(mcs),
        str(bandwidth),
        str(smoothing),
        str(not_sounding),
        str(aggregation),
        str(stbc),
        str(fec_coding),
        str(sgi),
        str(noise_floor),
        str(ampdu_cnt),
        str(channel),
        str(secondary_channel),
        str(local_timestamp),
        str(ant),
        str(sig_len),
        str(rx_format),
        str(effective_len),
        str(first_word_invalid),
        _csi_json(csi_values, effective_len),
    ]


def c5c6_payload_to_row(payload):
    unpacked = CSI_BINARY_C5C6_PAYLOAD.unpack(payload)
    idx = 0
    pkt_id = unpacked[idx]
    idx += 1
    mac = unpacked[idx]
    idx += 1
    rssi = unpacked[idx]
    idx += 1
    rate = unpacked[idx]
    idx += 1
    noise_floor, fft_gain, agc_gain, channel = unpacked[idx:idx + 4]
    idx += 4
    local_timestamp = unpacked[idx]
    idx += 1
    sig_len, rx_format = unpacked[idx:idx + 2]
    idx += 2
    length = unpacked[idx]
    idx += 1
    first_word_invalid = unpacked[idx]
    idx += 2
    csi_values = unpacked[idx:idx + CSI_BINARY_MAX_LEN]
    effective_len = min(int(length), len(csi_values), CSI_BINARY_MAX_LEN)

    return [
        'CSI_DATA',
        str(pkt_id),
        _mac_str(mac),
        str(rssi),
        str(rate),
        str(noise_floor),
        str(fft_gain),
        str(agc_gain),
        str(channel),
        str(local_timestamp),
        str(sig_len),
        str(rx_format),
        str(effective_len),
        str(first_word_invalid),
        _csi_json(csi_values, effective_len),
    ]


def _mac_bytes(mac_str):
    return bytes(int(part, 16) for part in mac_str.split(':'))


# Byte offsets from start of full frame (header + payload). +1 vs. the
# pre-v2 offsets below sig_len because sig_len widened uint8_t -> uint16_t.
_LEGACY_MAC_OFF = CSI_BINARY_HEADER.size + 4
_LEGACY_LEN_OFF = CSI_BINARY_HEADER.size + 33
_LEGACY_CHANNEL_OFF = CSI_BINARY_HEADER.size + 23
_C5C6_MAC_OFF = CSI_BINARY_HEADER.size + 4
_C5C6_LEN_OFF = CSI_BINARY_HEADER.size + 23


class BinaryStreamParser:
    """Fixed-size frame parser — avoids false magic matches inside CSI payload."""

    def __init__(self, expected_mac=None, expected_len=None, expected_channel=11):
        self._buf = bytearray()
        self._expected_mac = _mac_bytes(expected_mac) if expected_mac else None
        self._expected_len = expected_len
        self._expected_channel = expected_channel
        self.sync_shifts = 0

    def feed(self, data):
        self._buf.extend(data)

    def _frame_layout(self, layout):
        if layout == CSI_BINARY_LAYOUT_LEGACY:
            return CSI_BINARY_LEGACY_FRAME_SIZE, DATA_COLUMNS_NAMES, legacy_payload_to_row
        if layout == CSI_BINARY_LAYOUT_C5C6:
            return CSI_BINARY_C5C6_FRAME_SIZE, DATA_COLUMNS_NAMES_C5C6, c5c6_payload_to_row
        return None

    def _precheck_frame(self, frame_size, mac_off, len_off, channel_off=None):
        if len(self._buf) < frame_size:
            return 'need_more'

        magic, version, layout, _seq, payload_len, payload_crc16 = CSI_BINARY_HEADER.unpack_from(self._buf, 0)
        if magic != CSI_BINARY_MAGIC or version != CSI_BINARY_VERSION:
            return 'shift'

        spec = self._frame_layout(layout)
        if spec is None or spec[0] != frame_size:
            return 'shift'

        mac = bytes(self._buf[mac_off:mac_off + 6])
        if self._expected_mac and mac != self._expected_mac:
            return 'shift'

        length = struct.unpack_from('<H', self._buf, len_off)[0]
        if length == 0 or length > CSI_BINARY_MAX_LEN:
            return 'shift'
        if self._expected_len and length != self._expected_len:
            return 'shift'

        if channel_off is not None and self._expected_channel is not None:
            channel = self._buf[channel_off]
            if channel != self._expected_channel:
                return 'shift'

        # Cheap structural checks passed — now verify the payload wasn't
        # corrupted in transit (the one thing v1 of this protocol couldn't
        # detect at all).
        payload = bytes(self._buf[CSI_BINARY_HEADER.size:frame_size])
        if payload_len != len(payload) or crc16_xmodem(payload) != payload_crc16:
            return 'shift'

        return spec

    def pop_frame(self):
        """Returns ((columns, row, seq), discarded_bytes) or (None, discarded_bytes).

        `seq` is the firmware's RX-local monotonic frame counter — use it to
        detect frames lost between the CSI callback and the UART writer
        (e.g. a full internal queue), distinct from TX->RX ESP-NOW loss
        (tracked separately via the `id` payload field / CaptureValidator).
        """
        discarded = bytearray()

        while True:
            if len(self._buf) < CSI_BINARY_HEADER.size:
                return None, bytes(discarded)

            magic, version, layout, seq, _payload_len, _payload_crc16 = CSI_BINARY_HEADER.unpack_from(self._buf, 0)
            if magic == CSI_BINARY_MAGIC and version == CSI_BINARY_VERSION:
                spec = self._frame_layout(layout)
                if spec is not None:
                    frame_size, columns, to_row = spec
                    if layout == CSI_BINARY_LAYOUT_LEGACY:
                        mac_off, len_off, channel_off = _LEGACY_MAC_OFF, _LEGACY_LEN_OFF, _LEGACY_CHANNEL_OFF
                    else:
                        mac_off, len_off, channel_off = _C5C6_MAC_OFF, _C5C6_LEN_OFF, None

                    if len(self._buf) < frame_size:
                        return None, bytes(discarded)

                    check = self._precheck_frame(frame_size, mac_off, len_off, channel_off)
                    if check == 'need_more':
                        return None, bytes(discarded)
                    if check != 'shift':
                        payload = bytes(self._buf[CSI_BINARY_HEADER.size:frame_size])
                        del self._buf[:frame_size]
                        row, error = _row_from_payload(to_row, payload)
                        if row is not None:
                            return (columns, row, seq), bytes(discarded)

            discarded.append(self._buf[0])
            del self._buf[0:1]
            self.sync_shifts += 1


def _write_log_text(log_file_fd, data):
    if not data:
        return
    text = data.decode('utf-8', errors='replace')
    for line in text.splitlines():
        if line:
            log_file_fd.write(line + '\n')
    log_file_fd.flush()


def _write_log_line(log_file_fd, message, row=None):
    log_file_fd.write(message + '\n')
    if row is not None:
        log_file_fd.write(','.join(row) + '\n')
    log_file_fd.flush()


class CaptureValidator:
    def __init__(self, expected_mac, expected_len):
        self.expected_mac = expected_mac.lower() if expected_mac else None
        self.expected_len = expected_len
        self.saved = 0
        self.rejected = 0
        self.len_ok = 0
        self.len_bad = 0
        self.first_len_printed = False
        self.last_id = None
        self.sequence_received = 0
        self.sequence_missing = 0
        self.sequence_reordered = 0
        self.sequence_resets = 0
        # RX-local frame `seq` (v2 protocol) — tracks UART-side loss (e.g.
        # firmware's internal queue was full), independent of `id`'s
        # TX->RX ESP-NOW loss tracking above.
        self.last_uart_seq = None
        self.uart_seq_received = 0
        self.uart_seq_missing = 0
        self.uart_seq_reordered = 0
        self.uart_seq_resets = 0
        self._counts = {
            'element number is not equal': 0,
            'data is incomplete': 0,
            'csi_data_len is not equal': 0,
            'csi_len unexpected': 0,
            'csi_data empty': 0,
            'mac mismatch': 0,
            'frame decode error': 0,
        }

    def validate(self, columns, row):
        expected_cols = {len(DATA_COLUMNS_NAMES), len(DATA_COLUMNS_NAMES_C5C6)}
        if len(row) not in expected_cols:
            return False, 'element number is not equal', len(row), len(DATA_COLUMNS_NAMES)

        if len(row) != len(columns):
            return False, 'element number is not equal', len(row), len(columns)

        try:
            csi_data_len = int(row[-3])
        except ValueError:
            return False, 'data is incomplete', None, None

        try:
            csi_raw_data = json.loads(row[-1])
        except json.JSONDecodeError:
            return False, 'data is incomplete', csi_data_len, None

        if csi_data_len != len(csi_raw_data):
            return False, 'csi_data_len is not equal', csi_data_len, len(csi_raw_data)

        if csi_data_len == 0:
            return False, 'csi_data empty', csi_data_len, 0

        if self.expected_len and csi_data_len != self.expected_len:
            return False, 'csi_len unexpected', csi_data_len, self.expected_len

        if self.expected_mac and row[2].lower() != self.expected_mac:
            return False, 'mac mismatch', row[2], self.expected_mac

        return True, None, csi_data_len, len(csi_raw_data)

    def record_ok(self, csi_data_len, sequence_id):
        self.saved += 1
        self.len_ok += 1
        if self.last_id is not None:
            delta = (sequence_id - self.last_id) & 0xFFFFFFFF
            if delta == 0 or delta > 0x7FFFFFFF:
                # A sender reboot restarts app_main's counter at zero. Treat a
                # low new counter after an established stream as a new epoch.
                if sequence_id < 1000 and self.last_id >= 1000:
                    self.sequence_resets += 1
                    self.last_id = sequence_id
                else:
                    # Duplicate or stale frame; retain the newest sequence frontier.
                    self.sequence_reordered += 1
            else:
                self.sequence_missing += delta - 1
                self.last_id = sequence_id
        else:
            self.last_id = sequence_id
        self.sequence_received += 1
        if not self.first_len_printed:
            self.first_len_printed = True
            print('csi_len:', csi_data_len)

    def record_uart_seq(self, seq):
        """Track every frame pop_frame() returns, valid or not — a gap here
        means the firmware dropped a frame before it ever reached the UART
        (e.g. its internal queue was full), so it can never appear at all."""
        if self.last_uart_seq is not None:
            delta = (seq - self.last_uart_seq) & 0xFFFFFFFF
            if delta == 0 or delta > 0x7FFFFFFF:
                if seq < 1000 and self.last_uart_seq >= 1000:
                    self.uart_seq_resets += 1
                    self.last_uart_seq = seq
                else:
                    self.uart_seq_reordered += 1
            else:
                self.uart_seq_missing += delta - 1
                self.last_uart_seq = seq
        else:
            self.last_uart_seq = seq
        self.uart_seq_received += 1

    def record_bad(self, reason):
        self.rejected += 1
        if reason in self._counts:
            self._counts[reason] += 1
        if reason == 'csi_len unexpected':
            self.len_bad += 1

    def maybe_print(self, reason, message):
        count = self._counts.get(reason, 0)
        if count <= 2 or count % 100 == 0:
            print(message)

    def status_line(self, packets_seen, elapsed_s, sync_shifts=0):
        rate = self.saved / elapsed_s if elapsed_s > 0 else 0.0
        ok_pct = (100.0 * self.len_ok / self.saved) if self.saved else 0.0
        reject_pct = (100.0 * self.rejected / packets_seen) if packets_seen else 0.0
        sequence_expected = self.sequence_received + self.sequence_missing
        sequence_loss_pct = (
            100.0 * self.sequence_missing / sequence_expected
            if sequence_expected else 0.0
        )

        if self.saved == 0:
            health = 'BAD - no valid packets saved yet'
        elif self.rejected == 0:
            health = 'OK - full CSI capture'
        elif reject_pct < 0.5:
            health = 'OK - full CSI capture'
        elif self.len_bad == 0:
            health = 'WARN - occasional sync resync'
        else:
            health = 'BAD - mixed CSI lengths, check parser/firmware'

        uart_expected = self.uart_seq_received + self.uart_seq_missing
        uart_loss_pct = (
            100.0 * self.uart_seq_missing / uart_expected if uart_expected else 0.0
        )

        return (
            f'packets: {packets_seen} | saved: {self.saved} | rejected: {self.rejected} '
            f'({reject_pct:.2f}%) | len={self.expected_len or "any"}: {self.len_ok} ({ok_pct:.1f}%) | '
            f'rate: {rate:.1f} Hz | tx_missing: {self.sequence_missing}/{sequence_expected} '
            f'({sequence_loss_pct:.2f}%) | reordered: {self.sequence_reordered} | '
            f'tx_resets: {self.sequence_resets} | '
            f'uart_missing: {self.uart_seq_missing}/{uart_expected} ({uart_loss_pct:.2f}%) | '
            f'resync_bytes: {sync_shifts} | STATUS: {health}'
        )

    def summary(self):
        print('--- capture summary ---')
        print(f'saved: {self.saved}')
        print(f'rejected: {self.rejected}')
        sequence_expected = self.sequence_received + self.sequence_missing
        sequence_loss_pct = (
            100.0 * self.sequence_missing / sequence_expected
            if sequence_expected else 0.0
        )
        print(
            f'tx sequence loss: {self.sequence_missing}/{sequence_expected} '
            f'({sequence_loss_pct:.2f}%), reordered/duplicate: {self.sequence_reordered}, '
            f'tx resets: {self.sequence_resets}'
        )
        uart_expected = self.uart_seq_received + self.uart_seq_missing
        uart_loss_pct = (
            100.0 * self.uart_seq_missing / uart_expected if uart_expected else 0.0
        )
        print(
            f'uart (firmware-internal) frame loss: {self.uart_seq_missing}/{uart_expected} '
            f'({uart_loss_pct:.2f}%), reordered/duplicate: {self.uart_seq_reordered}, '
            f'resets: {self.uart_seq_resets}'
        )
        for reason, count in self._counts.items():
            if count:
                print(f'  {reason}: {count}')
        if self.saved == 0:
            print('RESULT: capture failed - no valid CSI rows written')
        elif self.rejected == 0 and self.len_ok == self.saved:
            print('RESULT: OK - full CSI data captured')
        elif self.len_ok > 0 and self.rejected < max(10, int(self.saved * 0.005)):
            print('RESULT: OK - full CSI data captured (minor resync drops)')
        elif self.len_ok > 0:
            print('RESULT: PARTIAL - some packets were invalid or truncated')
        else:
            print('RESULT: BAD - CSI arrays were not captured correctly')

    def stats_dict(self):
        """Transport-quality counters for callers to persist (e.g. into a
        collect_*.py manifest row) — same figures as status_line()/summary(),
        structured instead of printed."""
        sequence_expected = self.sequence_received + self.sequence_missing
        tx_missing_pct = (
            100.0 * self.sequence_missing / sequence_expected if sequence_expected else 0.0
        )
        uart_expected = self.uart_seq_received + self.uart_seq_missing
        uart_missing_pct = (
            100.0 * self.uart_seq_missing / uart_expected if uart_expected else 0.0
        )
        return {
            'saved': self.saved,
            'rejected': self.rejected,
            'tx_missing': self.sequence_missing,
            'tx_expected': sequence_expected,
            'tx_missing_pct': round(tx_missing_pct, 2),
            'tx_reordered': self.sequence_reordered,
            'tx_resets': self.sequence_resets,
            'uart_missing': self.uart_seq_missing,
            'uart_expected': uart_expected,
            'uart_missing_pct': round(uart_missing_pct, 2),
            'uart_reordered': self.uart_seq_reordered,
            'uart_resets': self.uart_seq_resets,
        }


def capture_binary_csi(port, baudrate, csv_writer, log_file_fd, csv_file=None,
                       expected_mac='1a:00:00:00:00:00', expected_len=EXPECTED_LEN_HT40,
                       report_interval=2.0, duration_sec=None, stats_out=None):
    parser = BinaryStreamParser(expected_mac=expected_mac, expected_len=expected_len)
    validator = CaptureValidator(expected_mac=expected_mac, expected_len=expected_len)
    ser = serial.Serial(port=port, baudrate=baudrate, bytesize=8, parity='N', stopbits=1, timeout=0.1)
    if not ser.is_open:
        print('open failed')
        return 1

    print('open success')
    print('Close idf.py monitor before capture — only one program can use the serial port.')
    if expected_len:
        print(f'Expecting CSI len={expected_len} on every valid packet (HT40 default).')
    if duration_sec:
        print(f'Auto-stop after {duration_sec}s')
    header_written = False
    packets_seen = 0
    start_time = time.time()
    last_report = start_time

    try:
        while True:
            if duration_sec and (time.time() - start_time) >= duration_sec:
                print('duration reached')
                break
            try:
                chunk = ser.read(4096)
            except serial.SerialException as exc:
                print('serial error:', exc)
                print('If idf.py monitor is open, close it (Ctrl+]) and rerun capture.')
                break
            if not chunk:
                continue
            parser.feed(chunk)
            while True:
                try:
                    frame, discarded = parser.pop_frame()
                except struct.error as exc:
                    validator.record_bad('frame decode error')
                    print('frame decode error', exc)
                    _write_log_line(log_file_fd, f'frame decode error: {exc}')
                    continue

                _write_log_text(log_file_fd, discarded)
                if frame is None:
                    break

                packets_seen += 1
                columns, row, seq = frame
                validator.record_uart_seq(seq)
                ok, reason, detail_a, detail_b = validator.validate(columns, row)
                if not ok:
                    validator.record_bad(reason)
                    if reason == 'element number is not equal':
                        validator.maybe_print(reason, f'element number is not equal {detail_a} {detail_b}')
                    elif reason == 'data is incomplete':
                        validator.maybe_print(reason, 'data is incomplete')
                    elif reason == 'csi_data_len is not equal':
                        validator.maybe_print(reason, f'csi_data_len is not equal {detail_a} {detail_b}')
                    elif reason == 'csi_len unexpected':
                        validator.maybe_print(reason, f'csi_len unexpected {detail_a} expected {detail_b}')
                    elif reason == 'csi_data empty':
                        validator.maybe_print(reason, 'csi_data empty')
                    elif reason == 'mac mismatch':
                        validator.maybe_print(reason, f'mac mismatch {detail_a} expected {detail_b}')
                    _write_log_line(log_file_fd, reason, row)
                    continue

                if not header_written:
                    csv_writer.writerow(columns)
                    header_written = True
                csv_writer.writerow(row)
                validator.record_ok(detail_a, int(row[1]))

            if csv_file is not None and validator.saved and validator.saved % 100 == 0:
                csv_file.flush()

            now = time.time()
            if now - last_report >= report_interval:
                print(validator.status_line(packets_seen, now - start_time, parser.sync_shifts))
                last_report = now
    except KeyboardInterrupt:
        print('stopped')
    except Exception as exc:
        print('capture error:', exc)
    finally:
        validator.summary()
        if parser.sync_shifts:
            print(f'resync_bytes: {parser.sync_shifts}')
        ser.close()
        if stats_out:
            stats = validator.stats_dict()
            stats['packets_seen'] = packets_seen
            stats['resync_bytes'] = parser.sync_shifts
            try:
                Path(stats_out).write_text(json.dumps(stats, indent=2))
            except OSError as exc:
                print(f'warning: could not write stats file {stats_out}: {exc}')
    reject_ratio = validator.rejected / max(1, packets_seen)
    return 0 if validator.saved and reject_ratio < 0.005 else 1


def main():
    parser = argparse.ArgumentParser(
        description='Read binary CSI from csi_recv and save ASCII CSV/log files')
    parser.add_argument('-p', '--port', required=True, help='Serial port of csi_recv')
    parser.add_argument('-b', '--baud', type=int, default=921600, help='Serial baud rate')
    parser.add_argument('-s', '--store', default='./csi_data.csv', help='Output CSV file')
    parser.add_argument('-l', '--log', default='./csi_data_log.txt', help='Non-CSI serial log file')
    parser.add_argument('--expected-len', type=int, default=EXPECTED_LEN_HT40,
                        help='Expected CSI len field (384 for HT40, 128 for HT20, 0 to disable)')
    parser.add_argument('--expected-mac', default='1a:00:00:00:00:00',
                        help='Expected transmitter MAC in CSV rows')
    parser.add_argument('--report-interval', type=float, default=2.0,
                        help='Seconds between capture health reports')
    parser.add_argument('--duration', type=float, default=None,
                        help='Stop capture automatically after N seconds')
    parser.add_argument('--stats-out', default='',
                        help='Write final capture-quality stats (resync/tx-missing/'
                             'uart-missing counters) as JSON to this path, for a '
                             'caller (e.g. collect_*.py) to read back and persist')

    args = parser.parse_args()
    expected_len = args.expected_len if args.expected_len > 0 else None

    # Serial startup noise can contain arbitrary bytes. Preserve its decoded
    # replacement characters in UTF-8 instead of failing on the Windows ANSI
    # code page while writing the diagnostic log.
    with (open(args.store, 'w', newline='', encoding='utf-8') as csv_file,
          open(args.log, 'w', encoding='utf-8', errors='replace') as log_file):
        csv_writer = csv.writer(csv_file)
        return capture_binary_csi(
            args.port,
            args.baud,
            csv_writer,
            log_file,
            csv_file,
            expected_mac=args.expected_mac,
            expected_len=expected_len,
            report_interval=args.report_interval,
            duration_sec=args.duration,
            stats_out=args.stats_out or None,
        )


if __name__ == '__main__':
    sys.exit(main())
