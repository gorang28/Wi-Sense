package com.wisense.resident.data.settings

import android.content.Context
import android.content.SharedPreferences
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Tiny preference surface for the Settings screen. Backed by
 * SharedPreferences (no DataStore dependency needed at this scope).
 */
@Singleton
class SettingsStore @Inject constructor(
    @ApplicationContext context: Context,
) {
    private val prefs: SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /** Show the persistent "monitoring active" notification. Default on. */
    private val _showMonitorNotification = MutableStateFlow(
        prefs.getBoolean(KEY_SHOW_MONITOR_NOTIFICATION, true),
    )
    val showMonitorNotification: StateFlow<Boolean> = _showMonitorNotification.asStateFlow()

    fun setShowMonitorNotification(show: Boolean) {
        prefs.edit().putBoolean(KEY_SHOW_MONITOR_NOTIFICATION, show).apply()
        _showMonitorNotification.value = show
    }

    /** This house's Firestore doc ID, created once on first sign-in and reused after. */
    private val _houseId = MutableStateFlow(prefs.getString(KEY_HOUSE_ID, null))
    val houseId: StateFlow<String?> = _houseId.asStateFlow()

    fun setHouseId(houseId: String) {
        prefs.edit().putString(KEY_HOUSE_ID, houseId).apply()
        _houseId.value = houseId
    }

    /** Logout: without this, a different account signing in on this device
     * would inherit the previous account's cached house. */
    fun clearHouseId() {
        prefs.edit().remove(KEY_HOUSE_ID).apply()
        _houseId.value = null
    }

    companion object {
        private const val PREFS_NAME = "wisense_resident_settings"
        private const val KEY_SHOW_MONITOR_NOTIFICATION = "show_monitor_notification"
        private const val KEY_HOUSE_ID = "house_id"
    }
}
