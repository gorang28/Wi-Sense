# SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Binary CSI frame layout shared with csi_recv/main/csi_binary_proto.h."""

import binascii
import struct

CSI_BINARY_MAGIC = 0xC511
CSI_BINARY_VERSION = 2
CSI_BINARY_LAYOUT_LEGACY = 0
CSI_BINARY_LAYOUT_C5C6 = 1
CSI_BINARY_MAX_LEN = 384

# Version 2: header grew a frame sequence number (RX-local, monotonic —
# detects frames dropped between the firmware's CSI callback and its UART
# writer task, e.g. a full internal queue) and a CRC16/XMODEM over the
# payload bytes (detects corruption inside a frame that magic/layout alone
# can't catch — see firmware/csi_recv/main/csi_binary_proto.h).
CSI_BINARY_HEADER = struct.Struct('<HBBIHH')

# sig_len widened from uint8_t ('B') to uint16_t ('H'): the hardware field
# is 12 bits (0-4095) and was silently wrapping mod 256.
CSI_BINARY_LEGACY_PAYLOAD = struct.Struct(
    '<I6sbb' + 'BBBBBBBBB' + 'bBBB' + 'I' + 'BHB' + 'HBB' + ('h' * CSI_BINARY_MAX_LEN)
)

CSI_BINARY_C5C6_PAYLOAD = struct.Struct(
    '<I6sbb' + 'bbbB' + 'IHB' + 'HBB' + ('h' * CSI_BINARY_MAX_LEN)
)


def crc16_xmodem(data):
    """CRC16/XMODEM (poly 0x1021, init 0) — matches firmware's crc16_xmodem()."""
    return binascii.crc_hqx(data, 0)

CSI_BINARY_LEGACY_FRAME_SIZE = CSI_BINARY_HEADER.size + CSI_BINARY_LEGACY_PAYLOAD.size
CSI_BINARY_C5C6_FRAME_SIZE = CSI_BINARY_HEADER.size + CSI_BINARY_C5C6_PAYLOAD.size

DATA_COLUMNS_NAMES = [
    'type', 'id', 'mac', 'rssi', 'rate', 'sig_mode', 'mcs', 'bandwidth', 'smoothing',
    'not_sounding', 'aggregation', 'stbc', 'fec_coding', 'sgi', 'noise_floor',
    'ampdu_cnt', 'channel', 'secondary_channel', 'local_timestamp', 'ant', 'sig_len',
    'rx_state', 'len', 'first_word', 'data',
]

DATA_COLUMNS_NAMES_C5C6 = [
    'type', 'id', 'mac', 'rssi', 'rate', 'noise_floor', 'fft_gain', 'agc_gain',
    'channel', 'local_timestamp', 'sig_len', 'rx_state', 'len', 'first_word', 'data',
]
