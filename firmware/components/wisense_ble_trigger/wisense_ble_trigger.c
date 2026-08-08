/*
 * WiSense BLE emergency trigger (RX -> phone), NimBLE GATT peripheral.
 */
#include <string.h>

#include "esp_log.h"
#include "sdkconfig.h"

#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

#include "wisense_ble_trigger.h"

static const char *TAG = "wisense_ble_trigger";

/* Private 128-bit UUIDs (not a registered SIG service). */
static const ble_uuid128_t s_svc_uuid =
    BLE_UUID128_INIT(0x01, 0x9a, 0xe0, 0xc3, 0x5b, 0x2f, 0x4a, 0x9e,
                     0x8d, 0x41, 0x2c, 0x6a, 0x00, 0x01, 0x9e, 0xf1);
static const ble_uuid128_t s_chr_uuid =
    BLE_UUID128_INIT(0x01, 0x9a, 0xe0, 0xc3, 0x5b, 0x2f, 0x4a, 0x9e,
                     0x8d, 0x41, 0x2c, 0x6a, 0x00, 0x02, 0x9e, 0xf1);

static uint16_t s_trigger_val_handle;
static uint16_t s_conn_handle = BLE_HS_CONN_HANDLE_NONE;
static bool s_notify_enabled;
static uint8_t s_own_addr_type;
static uint8_t s_trigger_value = WISENSE_BLE_TRIGGER_IDLE;

static void ble_trigger_advertise(void);

static int trigger_chr_access_cb(uint16_t conn_handle, uint16_t attr_handle,
                                 struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)conn_handle;
    (void)attr_handle;
    (void)arg;

    if (ctxt->op != BLE_GATT_ACCESS_OP_READ_CHR) {
        return BLE_ATT_ERR_UNLIKELY;
    }

    int rc = os_mbuf_append(ctxt->om, &s_trigger_value, sizeof(s_trigger_value));
    return rc == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}

static const struct ble_gatt_svc_def s_gatt_svcs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &s_svc_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid = &s_chr_uuid.u,
                .access_cb = trigger_chr_access_cb,
                .val_handle = &s_trigger_val_handle,
                .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY,
            },
            { 0 },
        },
    },
    { 0 },
};

static int ble_trigger_gap_event(struct ble_gap_event *event, void *arg)
{
    (void)arg;

    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        ESP_LOGI(TAG, "connect %s; status=%d",
                 event->connect.status == 0 ? "established" : "failed",
                 event->connect.status);
        if (event->connect.status != 0) {
            ble_trigger_advertise();
            break;
        }
        s_conn_handle = event->connect.conn_handle;
        break;

    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI(TAG, "disconnect; reason=%d", event->disconnect.reason);
        s_conn_handle = BLE_HS_CONN_HANDLE_NONE;
        s_notify_enabled = false;
        ble_trigger_advertise();
        break;

    case BLE_GAP_EVENT_ADV_COMPLETE:
        ble_trigger_advertise();
        break;

    case BLE_GAP_EVENT_SUBSCRIBE:
        if (event->subscribe.attr_handle == s_trigger_val_handle) {
            s_notify_enabled = event->subscribe.cur_notify;
            ESP_LOGI(TAG, "phone %s notifications", s_notify_enabled ? "subscribed to" : "unsubscribed from");
        }
        break;

    default:
        break;
    }

    return 0;
}

static void ble_trigger_advertise(void)
{
    struct ble_hs_adv_fields fields = {0};
    const char *name = ble_svc_gap_device_name();

    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.tx_pwr_lvl_is_present = 1;
    fields.tx_pwr_lvl = BLE_HS_ADV_TX_PWR_LVL_AUTO;
    fields.name = (uint8_t *)name;
    fields.name_len = strlen(name);
    fields.name_is_complete = 1;

    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv_set_fields failed rc=%d", rc);
        return;
    }

    struct ble_gap_adv_params adv_params = {0};
    adv_params.conn_mode = BLE_GAP_CONN_MODE_UND;
    adv_params.disc_mode = BLE_GAP_DISC_MODE_GEN;

    rc = ble_gap_adv_start(s_own_addr_type, NULL, BLE_HS_FOREVER, &adv_params, ble_trigger_gap_event, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv_start failed rc=%d", rc);
    }
}

static void ble_trigger_on_sync(void)
{
    int rc = ble_hs_id_infer_auto(0, &s_own_addr_type);
    if (rc != 0) {
        ESP_LOGE(TAG, "id_infer_auto failed rc=%d", rc);
        return;
    }
    ble_trigger_advertise();
}

static void ble_trigger_on_reset(int reason)
{
    ESP_LOGW(TAG, "NimBLE stack reset; reason=%d", reason);
}

static void ble_trigger_host_task(void *param)
{
    (void)param;
    nimble_port_run();
    nimble_port_freertos_deinit();
}

esp_err_t wisense_ble_trigger_init(void)
{
    esp_err_t err = nimble_port_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nimble_port_init failed: %s", esp_err_to_name(err));
        return err;
    }

    ble_hs_cfg.sync_cb = ble_trigger_on_sync;
    ble_hs_cfg.reset_cb = ble_trigger_on_reset;

    ble_svc_gap_init();
    ble_svc_gatt_init();

    int rc = ble_gatts_count_cfg(s_gatt_svcs);
    if (rc != 0 || (rc = ble_gatts_add_svcs(s_gatt_svcs)) != 0) {
        ESP_LOGE(TAG, "gatt service registration failed rc=%d", rc);
        return ESP_FAIL;
    }

    rc = ble_svc_gap_device_name_set(CONFIG_WISENSE_BLE_TRIGGER_DEVICE_NAME);
    if (rc != 0) {
        ESP_LOGE(TAG, "device_name_set failed rc=%d", rc);
        return ESP_FAIL;
    }

    nimble_port_freertos_init(ble_trigger_host_task);

    ESP_LOGI(TAG, "BLE trigger ready, advertising as \"%s\"", CONFIG_WISENSE_BLE_TRIGGER_DEVICE_NAME);
    return ESP_OK;
}

esp_err_t wisense_ble_trigger_send(wisense_ble_trigger_evt_t evt)
{
    s_trigger_value = (uint8_t)evt;

    if (s_conn_handle == BLE_HS_CONN_HANDLE_NONE || !s_notify_enabled) {
        ESP_LOGW(TAG, "trigger evt=%d set but no phone connected/subscribed", (int)evt);
        return ESP_OK;
    }

    struct os_mbuf *om = ble_hs_mbuf_from_flat(&s_trigger_value, sizeof(s_trigger_value));
    if (om == NULL) {
        ESP_LOGE(TAG, "mbuf alloc failed for trigger notify");
        return ESP_ERR_NO_MEM;
    }

    int rc = ble_gatts_notify_custom(s_conn_handle, s_trigger_val_handle, om);
    if (rc != 0) {
        ESP_LOGE(TAG, "notify failed rc=%d", rc);
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "trigger evt=%d notified to phone", (int)evt);
    return ESP_OK;
}
