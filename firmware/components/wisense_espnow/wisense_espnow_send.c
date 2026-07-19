/*
 * ESP-NOW emergency sender (RX → TX / ESP32-CAM).
 *
 * Uses the same Wi-Fi channel and PMK as the CSI stack; payload is distinct
 * from the 4-byte CSI counter so TX CSI transmit is unaffected.
 */
#include <string.h>

#include "esp_check.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_wifi.h"
#include "sdkconfig.h"

#include "wisense_espnow_proto.h"
#include "wisense_espnow_send.h"

static const char *TAG = "wisense_espnow_tx";

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(6, 0, 0)
#define ESP_IF_WIFI_STA ESP_MAC_WIFI_STA
#endif

static bool s_ready;
static uint8_t s_seq;
static uint8_t s_tx_mac[6] = {
    CONFIG_WISENSE_ESPNOW_TX_MAC_ADDR0,
    CONFIG_WISENSE_ESPNOW_TX_MAC_ADDR1,
    CONFIG_WISENSE_ESPNOW_TX_MAC_ADDR2,
    CONFIG_WISENSE_ESPNOW_TX_MAC_ADDR3,
    CONFIG_WISENSE_ESPNOW_TX_MAC_ADDR4,
    CONFIG_WISENSE_ESPNOW_TX_MAC_ADDR5,
};

static esp_err_t wifi_espnow_setup(void)
{
    esp_err_t err = esp_netif_init();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }

    err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&cfg), TAG, "wifi init");

    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "wifi mode");
    ESP_RETURN_ON_ERROR(esp_wifi_set_storage(WIFI_STORAGE_RAM), TAG, "wifi storage");
    ESP_RETURN_ON_ERROR(esp_wifi_set_bandwidth(ESP_IF_WIFI_STA, WIFI_BW_HT40), TAG, "bandwidth");
    ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "wifi start");
    ESP_RETURN_ON_ERROR(esp_wifi_set_ps(WIFI_PS_NONE), TAG, "wifi ps");
    ESP_RETURN_ON_ERROR(esp_wifi_set_channel(CONFIG_WISENSE_ESPNOW_WIFI_CHANNEL,
                                             WIFI_SECOND_CHAN_BELOW),
                        TAG, "channel");

    ESP_RETURN_ON_ERROR(esp_now_init(), TAG, "esp_now init");
    ESP_RETURN_ON_ERROR(esp_now_set_pmk((uint8_t *)"pmk1234567890123"), TAG, "pmk");

    esp_now_peer_info_t peer = {
        .channel = CONFIG_WISENSE_ESPNOW_WIFI_CHANNEL,
        .ifidx = WIFI_IF_STA,
        .encrypt = false,
    };
    memcpy(peer.peer_addr, s_tx_mac, 6);

    ESP_RETURN_ON_ERROR(esp_now_add_peer(&peer), TAG, "add peer");

    esp_now_rate_config_t rate = {
        .phymode = WIFI_PHY_MODE_HT40,
        .rate = WIFI_PHY_RATE_MCS0_LGI,
        .ersu = false,
        .dcm = false,
    };
    ESP_RETURN_ON_ERROR(esp_now_set_peer_rate_config(peer.peer_addr, &rate), TAG, "peer rate");

    uint8_t sta_mac[6];
    esp_read_mac(sta_mac, ESP_MAC_WIFI_STA);
    ESP_LOGI(TAG, "ESP-NOW sender ready (STA " MACSTR " → TX " MACSTR " ch=%d)",
             MAC2STR(sta_mac), MAC2STR(s_tx_mac), CONFIG_WISENSE_ESPNOW_WIFI_CHANNEL);
    return ESP_OK;
}

esp_err_t wisense_espnow_emerg_sender_init(void)
{
    if (s_ready) {
        return ESP_OK;
    }

    ESP_RETURN_ON_ERROR(wifi_espnow_setup(), TAG, "setup");
    s_seq = 0;
    s_ready = true;
    return ESP_OK;
}

esp_err_t wisense_espnow_emerg_send(wisense_espnow_msg_type_t type)
{
    ESP_RETURN_ON_FALSE(s_ready, ESP_ERR_INVALID_STATE, TAG, "not init");

    wisense_espnow_emerg_pkt_t pkt;
    wisense_espnow_emerg_build(type, s_seq++, &pkt);

    esp_err_t err = esp_now_send(s_tx_mac, (const uint8_t *)&pkt, sizeof(pkt));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_now_send failed: %s", esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "Sent emergency ESP-NOW type=%u seq=%u to " MACSTR,
             (unsigned)type, (unsigned)pkt.seq, MAC2STR(s_tx_mac));
    return ESP_OK;
}
