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


# Byte offsets from start of full frame (header + payload).
_LEGACY_MAC_OFF = CSI_BINARY_HEADER.size + 4
_LEGACY_LEN_OFF = CSI_BINARY_HEADER.size + 32
_LEGACY_CHANNEL_OFF = CSI_BINARY_HEADER.size + 23
_C5C6_MAC_OFF = CSI_BINARY_HEADER.size + 4
_C5C6_LEN_OFF = CSI_BINARY_HEADER.size + 22


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

        magic, version, layout = CSI_BINARY_HEADER.unpack_from(self._buf, 0)
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

        return spec

    def pop_frame(self):
        discarded = bytearray()

        while True:
            if len(self._buf) < CSI_BINARY_HEADER.size:
                return None, bytes(discarded)

            magic, version, layout = CSI_BINARY_HEADER.unpack_from(self._buf, 0)
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
                            return (columns, row), bytes(discarded)

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

    def record_ok(self, csi_data_len):
        self.saved += 1
        self.len_ok += 1
        if not self.first_len_printed:
            self.first_len_printed = True
            print('csi_len:', csi_data_len)

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

        return (
            f'packets: {packets_seen} | saved: {self.saved} | rejected: {self.rejected} '
            f'({reject_pct:.2f}%) | len={self.expected_len or "any"}: {self.len_ok} ({ok_pct:.1f}%) | '
            f'rate: {rate:.1f} Hz | resync_bytes: {sync_shifts} | STATUS: {health}'
        )

    def summary(self):
        print('--- capture summary ---')
        print(f'saved: {self.saved}')
        print(f'rejected: {self.rejected}')
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


def capture_binary_csi(port, baudrate, csv_writer, log_file_fd, csv_file=None,
                       expected_mac='1a:00:00:00:00:00', expected_len=EXPECTED_LEN_HT40,
                       report_interval=2.0, duration_sec=None):
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
                columns, row = frame
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
                validator.record_ok(detail_a)

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

    args = parser.parse_args()
    expected_len = args.expected_len if args.expected_len > 0 else None

    with open(args.store, 'w', newline='') as csv_file, open(args.log, 'w') as log_file:
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
        )


if __name__ == '__main__':
    sys.exit(main())
