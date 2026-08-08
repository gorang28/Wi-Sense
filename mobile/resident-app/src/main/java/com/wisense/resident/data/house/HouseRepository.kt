package com.wisense.resident.data.house

import com.google.firebase.firestore.FieldValue
import com.google.firebase.firestore.FirebaseFirestore
import com.wisense.resident.data.settings.SettingsStore
import com.wisense.shared.firebase.AuthClient
import kotlinx.coroutines.tasks.await
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Owns the resident's single house doc — created once on first sign-in,
 * ID cached locally so we don't create a new one every launch. Hobby-scope:
 * one house per resident, caregivers added by entering their 6-digit code
 * (shared out of band), no invite links.
 */
@Singleton
class HouseRepository @Inject constructor(
    private val settingsStore: SettingsStore,
) {
    private val db get() = FirebaseFirestore.getInstance()

    /** Returns the existing house ID, or creates one and caches it. */
    suspend fun ensureHouse(): String {
        settingsStore.houseId.value?.let { return it }

        val uid = AuthClient.currentUser?.uid ?: error("not signed in")
        // The code IS the doc ID — short and easy to read aloud/retype
        // between two phones, instead of a long Firestore auto-ID. Hobby
        // scope: no uniqueness check before writing, collision odds are
        // negligible at this scale.
        val code = (100000..999999).random().toString()
        db.collection("wisense_houses").document(code).set(
            mapOf(
                "ownerId" to uid,
                "name" to "My house",
                "residentDeviceBleId" to null,
                "caregiverIds" to emptyList<String>(),
                "createdAt" to FieldValue.serverTimestamp(),
            ),
        ).await()

        settingsStore.setHouseId(code)
        return code
    }

    /**
     * Resolves a caregiver's 6-digit code to their real Firebase UID via
     * wisense_caregiver_codes, then stores that UID — Firestore security
     * rules check request.auth.uid against caregiverIds directly, so the
     * real UID (not the code) is what has to end up in this array.
     */
    suspend fun addCaregiverByCode(caregiverCode: String) {
        val houseId = settingsStore.houseId.value ?: error("no house yet")
        val codeSnap = db.collection("wisense_caregiver_codes").document(caregiverCode).get().await()
        val uid = codeSnap.getString("uid") ?: error("no caregiver found for code $caregiverCode")
        db.collection("wisense_houses").document(houseId)
            .update("caregiverIds", FieldValue.arrayUnion(uid))
            .await()
    }

    suspend fun setResidentDeviceBleId(bleMac: String) {
        val houseId = settingsStore.houseId.value ?: return
        db.collection("wisense_houses").document(houseId)
            .update("residentDeviceBleId", bleMac)
            .await()
    }
}
