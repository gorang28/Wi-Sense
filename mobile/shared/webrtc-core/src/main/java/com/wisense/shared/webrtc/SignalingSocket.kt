package com.wisense.shared.webrtc

import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket

/**
 * Phase 3's signaling: no cloud, no discovery — the caregiver types the
 * resident's local IP by hand, and one SDP (with ICE candidates already
 * baked in, see WebRtcClient.awaitIceGatheringComplete) crosses the wire
 * each direction over a plain length-prefixed TCP socket. Phase 4 replaces
 * this with Firestore; this exists only to prove the WebRTC media path
 * before building real signaling infrastructure.
 */
sealed class SignalingMessage(val type: String, val sdp: String) {
    class Offer(sdp: String) : SignalingMessage("offer", sdp)
    class Answer(sdp: String) : SignalingMessage("answer", sdp)
}

fun SignalingMessage.toJson(): String =
    JSONObject().put("type", type).put("sdp", sdp).toString()

fun signalingMessageFromJson(json: String): SignalingMessage {
    val obj = JSONObject(json)
    val sdp = obj.getString("sdp")
    return when (val type = obj.getString("type")) {
        "offer" -> SignalingMessage.Offer(sdp)
        "answer" -> SignalingMessage.Answer(sdp)
        else -> error("unknown signaling message type: $type")
    }
}

/** Resident side: listens for the one caregiver connection. */
class SignalingServer(private val port: Int = DEFAULT_PORT) {
    private var serverSocket: ServerSocket? = null
    private var clientSocket: Socket? = null

    suspend fun awaitConnection() = withContext(Dispatchers.IO) {
        Log.d(TAG, "server: binding port $port, awaiting caregiver connection…")
        val server = ServerSocket()
        server.reuseAddress = true
        server.bind(InetSocketAddress(port))
        serverSocket = server
        clientSocket = server.accept()
        Log.d(TAG, "server: caregiver connected from ${clientSocket?.remoteSocketAddress}")
    }

    suspend fun send(message: SignalingMessage) = withContext(Dispatchers.IO) {
        Log.d(TAG, "server: sending ${message.type} (${message.sdp.length} chars)")
        writeFramed((clientSocket ?: error("not connected")).getOutputStream(), message.toJson())
    }

    suspend fun receive(): SignalingMessage = withContext(Dispatchers.IO) {
        val msg = signalingMessageFromJson(readFramed((clientSocket ?: error("not connected")).getInputStream()))
        Log.d(TAG, "server: received ${msg.type} (${msg.sdp.length} chars)")
        msg
    }

    fun close() {
        Log.d(TAG, "server: closing")
        runCatching { clientSocket?.close() }
        runCatching { serverSocket?.close() }
    }

    companion object {
        const val DEFAULT_PORT = 8890
    }
}

/** Caregiver side: connects to the resident's manually-entered IP. */
class SignalingClient {
    private var socket: Socket? = null

    suspend fun connect(host: String, port: Int = SignalingServer.DEFAULT_PORT) =
        withContext(Dispatchers.IO) {
            Log.d(TAG, "client: connecting to $host:$port…")
            val s = Socket()
            s.connect(InetSocketAddress(host, port), CONNECT_TIMEOUT_MS)
            socket = s
            Log.d(TAG, "client: connected")
        }

    suspend fun send(message: SignalingMessage) = withContext(Dispatchers.IO) {
        Log.d(TAG, "client: sending ${message.type} (${message.sdp.length} chars)")
        writeFramed((socket ?: error("not connected")).getOutputStream(), message.toJson())
    }

    suspend fun receive(): SignalingMessage = withContext(Dispatchers.IO) {
        val msg = signalingMessageFromJson(readFramed((socket ?: error("not connected")).getInputStream()))
        Log.d(TAG, "client: received ${msg.type} (${msg.sdp.length} chars)")
        msg
    }

    fun close() {
        Log.d(TAG, "client: closing")
        runCatching { socket?.close() }
    }

    companion object {
        private const val CONNECT_TIMEOUT_MS = 10_000
    }
}

private fun writeFramed(out: OutputStream, text: String) {
    val bytes = text.toByteArray(Charsets.UTF_8)
    val dos = DataOutputStream(out)
    dos.writeInt(bytes.size)
    dos.write(bytes)
    dos.flush()
}

private fun readFramed(input: InputStream): String {
    val dis = DataInputStream(input)
    val len = dis.readInt()
    require(len in 0..MAX_MESSAGE_BYTES) { "signaling message implausibly large: $len bytes" }
    val bytes = ByteArray(len)
    dis.readFully(bytes)
    return String(bytes, Charsets.UTF_8)
}

// Generous headroom over a real SDP+baked-in-ICE-candidates blob (typically a few KB).
private const val MAX_MESSAGE_BYTES = 1 shl 20
private const val TAG = "SignalingSocket"
