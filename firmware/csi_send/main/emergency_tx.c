/*
 * TX-side ESP-NOW glue for fall emergency packets from RX.
 */
#include "esp_check.h"
#include "esp_log.h"

#include "wisense_emergency_tx.h"
#include "wisense_espnow_proto.h"
#include "wisense_espnow_recv.h"

static const char *TAG = "emergency_tx";

static void on_emergency_espnow(wisense_espnow_msg_type_t type, void *ctx)
{
    (void)ctx;

    switch (type) {
    case WISENSE_ESPNOW_MSG_FALL_ALERT:
        wisense_emergency_tx_on_alert();
        break;
    case WISENSE_ESPNOW_MSG_FALL_CANCEL:
        wisense_emergency_tx_on_cancel();
        break;
    default:
        break;
    }
}

esp_err_t emergency_tx_init(void)
{
    ESP_RETURN_ON_ERROR(wisense_emergency_tx_init(), TAG, "orchestrator");
    return wisense_espnow_emerg_receiver_register(on_emergency_espnow, NULL);
}
