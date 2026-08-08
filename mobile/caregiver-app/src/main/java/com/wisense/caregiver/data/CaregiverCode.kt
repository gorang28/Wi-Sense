package com.wisense.caregiver.data

import com.google.firebase.firestore.FirebaseFirestore
import com.google.firebase.firestore.SetOptions
import com.wisense.shared.firebase.AuthClient
import kotlinx.coroutines.tasks.await

/**
 * A short, easy-to-read-aloud stand-in for this caregiver's Firebase UID —
 * the raw UID has no copy button on the House Link screen and is prone to
 * transcription typos (e.g. l/I) when a resident retypes it by hand.
 * Generated once and cached on the user doc; wisense_caregiver_codes is the
 * reverse lookup a resident's app uses to resolve a code back to the UID
 * that Firestore rules actually check.
 */
suspend fun ensureCaregiverCode(): String {
    val uid = AuthClient.currentUser?.uid ?: error("not signed in")
    val db = FirebaseFirestore.getInstance()
    val userDoc = db.collection("wisense_users").document(uid)

    userDoc.get().await().getString("caregiverCode")?.let { return it }

    val code = (100000..999999).random().toString()
    userDoc.set(mapOf("caregiverCode" to code), SetOptions.merge()).await()
    db.collection("wisense_caregiver_codes").document(code)
        .set(mapOf("uid" to uid))
        .await()
    return code
}
