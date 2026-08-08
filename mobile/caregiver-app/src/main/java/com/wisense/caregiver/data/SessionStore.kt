package com.wisense.caregiver.data

import android.content.Context
import android.content.SharedPreferences

/** Which house this caregiver is linked to, cached locally after linking once. */
class SessionStore(context: Context) {
    private val prefs: SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    var houseId: String?
        get() = prefs.getString(KEY_HOUSE_ID, null)
        set(value) {
            prefs.edit().putString(KEY_HOUSE_ID, value).apply()
        }

    companion object {
        private const val PREFS_NAME = "wisense_caregiver_session"
        private const val KEY_HOUSE_ID = "house_id"
    }
}
