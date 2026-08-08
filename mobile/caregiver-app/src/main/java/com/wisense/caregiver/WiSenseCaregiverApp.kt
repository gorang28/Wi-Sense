package com.wisense.caregiver

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.media.AudioAttributes
import android.media.RingtoneManager
import com.wisense.caregiver.fcm.WiSenseMessagingService

class WiSenseCaregiverApp : Application() {
    override fun onCreate() {
        super.onCreate()
        // Notification channels are immutable once created — recreating this
        // with the same ID on every launch does NOT update sound/vibration
        // for a channel that already exists on the device (confirmed
        // on-device: silent notifications despite IMPORTANCE_HIGH, on a
        // phone reinstalled many times this session). Explicit sound/
        // vibration here only actually takes effect for a channel ID that's
        // new to the device.
        val channel = NotificationChannel(
            WiSenseMessagingService.ALERT_CHANNEL_ID,
            "Emergency alerts",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = "Full-screen alert when a fall is detected"
            enableVibration(true)
            vibrationPattern = longArrayOf(0, 1000, 500, 1000, 500, 1000)
            setSound(
                RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE),
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build(),
            )
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }
}
