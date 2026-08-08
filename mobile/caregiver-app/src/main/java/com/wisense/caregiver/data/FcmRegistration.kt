package com.wisense.caregiver.data

import com.google.firebase.firestore.FieldValue
import com.google.firebase.firestore.FirebaseFirestore
import com.google.firebase.firestore.SetOptions
import com.google.firebase.messaging.FirebaseMessaging
import com.wisense.shared.firebase.AuthClient
import kotlinx.coroutines.tasks.await

/**
 * Registers this device's current FCM token against the signed-in user's
 * doc — called once after sign-in, and again from the messaging service's
 * onNewToken whenever the token rotates. Uses set(merge) rather than
 * update() so it can't fail if called before the user doc otherwise exists.
 */
suspend fun registerFcmToken() {
    val uid = AuthClient.currentUser?.uid ?: return
    val token = FirebaseMessaging.getInstance().token.await()
    FirebaseFirestore.getInstance().collection("wisense_users").document(uid)
        .set(mapOf("fcmTokens" to FieldValue.arrayUnion(token)), SetOptions.merge())
        .await()
}
