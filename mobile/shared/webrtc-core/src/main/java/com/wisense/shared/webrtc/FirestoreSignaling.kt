package com.wisense.shared.webrtc

import com.google.firebase.firestore.DocumentReference
import com.google.firebase.firestore.DocumentSnapshot
import com.google.firebase.firestore.FirebaseFirestore
import com.google.firebase.firestore.Query
import com.google.firebase.firestore.QuerySnapshot
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.tasks.await

/**
 * Phase 4 signaling — same [SignalingMessage] shape as Phase 3's TCP-based
 * SignalingServer/SignalingClient, carried through Firestore documents
 * instead so it works across any two networks, not just same-WiFi.
 * WebRtcClient itself doesn't change either way; only how the offer/answer
 * text crosses from one phone to the other.
 */
object FirestoreSignaling {
    private val db get() = FirebaseFirestore.getInstance()

    fun emergenciesCollection() = db.collection("wisense_emergencies")

    private fun signalingDoc(emergencyId: String, peerRole: String) =
        emergenciesCollection().document(emergencyId).collection("signaling").document(peerRole)

    suspend fun send(emergencyId: String, message: SignalingMessage) {
        val peerRole = if (message is SignalingMessage.Offer) "offer" else "answer"
        signalingDoc(emergencyId, peerRole)
            .set(mapOf("type" to message.type, "sdp" to message.sdp))
            .await()
    }

    /** Suspends until the offer/answer doc at [peerRole] exists, then returns it. */
    suspend fun awaitMessage(emergencyId: String, peerRole: String): SignalingMessage =
        signalingDoc(emergencyId, peerRole).snapshots()
            .filterNotNull()
            .first { it.exists() }
            .let { snap ->
                val sdp = snap.getString("sdp") ?: error("signaling doc missing sdp")
                when (val type = snap.getString("type")) {
                    "offer" -> SignalingMessage.Offer(sdp)
                    "answer" -> SignalingMessage.Answer(sdp)
                    else -> error("signaling doc has invalid type: $type")
                }
            }
}

/** Cold flow of a document's snapshots; keeps listening until the collector cancels. */
fun DocumentReference.snapshots(): Flow<DocumentSnapshot?> = callbackFlow {
    val registration = addSnapshotListener { snapshot, error ->
        if (error != null) {
            close(error)
            return@addSnapshotListener
        }
        trySend(snapshot)
    }
    awaitClose { registration.remove() }
}

/** Cold flow of a query's snapshots; keeps listening until the collector cancels. */
fun Query.snapshots(): Flow<QuerySnapshot?> = callbackFlow {
    val registration = addSnapshotListener { snapshot, error ->
        if (error != null) {
            close(error)
            return@addSnapshotListener
        }
        trySend(snapshot)
    }
    awaitClose { registration.remove() }
}
