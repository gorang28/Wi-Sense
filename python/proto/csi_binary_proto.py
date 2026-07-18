# SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Binary CSI frame layout shared with csi_recv/main/csi_binary_proto.h."""

import struct

CSI_BINARY_MAGIC = 0xC511
CSI_BINARY_VERSION = 1
CSI_BINARY_LAYOUT_LEGACY = 0
CSI_BINARY_LAYOUT_C5C6 = 1
CSI_BINARY_MAX_LEN = 384

CSI_BINARY_HEADER = struct.Struct('<HBB')

CSI_BINARY_LEGACY_PAYLOAD = struct.Struct(
    '<I6sbb' + 'BBBBBBBBB' + 'bBBB' + 'I' + 'BBB' + 'HBB' + ('h' * CSI_BINARY_MAX_LEN)
)

CSI_BINARY_C5C6_PAYLOAD = struct.Struct(
    '<I6sbb' + 'bbbB' + 'IBB' + 'HBB' + ('h' * CSI_BINARY_MAX_LEN)
)

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
