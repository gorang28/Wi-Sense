/*
 * WiSense ESP-NOW emergency packet helpers.
 */
#include <string.h>

#include "wisense_espnow_proto.h"

bool wisense_espnow_emerg_parse(const uint8_t *data, int len, wisense_espnow_emerg_pkt_t *out)
{
    if (data == NULL || len < (int)WISENSE_ESPNOW_EMERG_PKT_LEN) {
        return false;
    }

    wisense_espnow_emerg_pkt_t pkt;
    memcpy(&pkt, data, sizeof(pkt));

    if (pkt.magic != WISENSE_ESPNOW_EMERG_MAGIC) {
        return false;
    }
    if (pkt.type != WISENSE_ESPNOW_MSG_FALL_ALERT &&
        pkt.type != WISENSE_ESPNOW_MSG_FALL_CANCEL) {
        return false;
    }

    if (out != NULL) {
        *out = pkt;
    }
    return true;
}

void wisense_espnow_emerg_build(wisense_espnow_msg_type_t type, uint8_t seq,
                                wisense_espnow_emerg_pkt_t *out)
{
    if (out == NULL) {
        return;
    }
    out->magic = WISENSE_ESPNOW_EMERG_MAGIC;
    out->type = (uint8_t)type;
    out->seq = seq;
    out->reserved = 0;
}
