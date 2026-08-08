package com.wisense.resident

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import dagger.hilt.android.HiltAndroidApp

@HiltAndroidApp
class WiSenseResidentApp : Application() {

    override fun onCreate() {
        super.onCreate()
        createMonitorChannel()
    }

    private fun createMonitorChannel() {
        val channel = NotificationChannel(
            MONITOR_CHANNEL_ID,
            getString(R.string.monitor_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = getString(R.string.monitor_channel_description)
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    companion object {
        const val MONITOR_CHANNEL_ID = "wisense_monitor"
    }
}
