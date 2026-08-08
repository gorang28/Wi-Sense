package com.wisense.caregiver.fcm

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationManagerCompat

/** The "Decline" action on the incoming-call-style alert — just dismisses it. */
class NotificationDismissReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        NotificationManagerCompat.from(context).cancel(WiSenseMessagingService.ALERT_NOTIFICATION_ID)
    }
}
