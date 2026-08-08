package com.wisense.caregiver.fcm

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.Person
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.wisense.caregiver.MainActivity
import com.wisense.caregiver.R
import com.wisense.caregiver.data.registerFcmToken
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * Data messages only (never plain notification messages) so we control the
 * full-screen incoming-call-style presentation ourselves, matching doc §10 —
 * a plain notification message would be auto-displayed by the OS with no
 * way to make it full-screen or route the tap into the live stream.
 *
 * Uses setFullScreenIntent + CallStyle together: on-device testing found
 * CallStyle alone (no full-screen-intent) never showed the banner at all on
 * this device/Android version, locked or unlocked — full-screen-intent
 * turned out to be necessary for the notification to display, not just for
 * auto-launching. The tradeoff is the known one: on a locked screen this
 * skips the Answer/Decline banner and jumps straight into the app.
 */
class WiSenseMessagingService : FirebaseMessagingService() {

    private val scope = CoroutineScope(Dispatchers.IO)

    override fun onNewToken(token: String) {
        scope.launch { registerFcmToken() }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        Log.d(TAG, "onMessageReceived: data=${message.data}")
        when (message.data["type"]) {
            "emergency_active" -> showFullScreenAlert()
            "emergency_resolved" -> clearAlert()
            else -> Log.w(TAG, "onMessageReceived: unrecognized type ${message.data["type"]}")
        }
    }

    private fun showFullScreenAlert() {
        try {
            val openApp = PendingIntent.getActivity(
                this,
                0,
                Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
            val decline = PendingIntent.getBroadcast(
                this,
                0,
                Intent(this, NotificationDismissReceiver::class.java),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
            val caller = Person.Builder().setName("Fall detected").build()
            val notification = NotificationCompat.Builder(this, ALERT_CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentText("Tap to view live video")
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_CALL)
                .setFullScreenIntent(openApp, true)
                .setContentIntent(openApp)
                .setAutoCancel(true)
                .setOngoing(true)
                .setStyle(NotificationCompat.CallStyle.forIncomingCall(caller, decline, openApp))
                .build()
            (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
                .notify(ALERT_NOTIFICATION_ID, notification)
            Log.d(TAG, "showFullScreenAlert: notify() called")
        } catch (e: Exception) {
            Log.e(TAG, "showFullScreenAlert failed", e)
        }
    }

    private fun clearAlert() {
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager).cancel(ALERT_NOTIFICATION_ID)
    }

    companion object {
        // v2: bumped so a device that already has the old "wisense_alert"
        // channel (immutable once created — see WiSenseCaregiverApp) picks
        // up the new sound/vibration config instead of inheriting whatever
        // state the old channel was left in.
        const val ALERT_CHANNEL_ID = "wisense_alert_v2"
        const val ALERT_NOTIFICATION_ID = 2001
        private const val TAG = "WiSenseMessagingService"
    }
}
