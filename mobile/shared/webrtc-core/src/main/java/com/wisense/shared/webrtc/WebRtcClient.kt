package com.wisense.shared.webrtc

import android.content.Context
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CaptureRequest
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.view.Surface
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.withTimeout
import org.webrtc.AudioTrack
import org.webrtc.Camera2Enumerator
import org.webrtc.CameraVideoCapturer
import org.webrtc.DataChannel
import org.webrtc.DefaultVideoDecoderFactory
import org.webrtc.DefaultVideoEncoderFactory
import org.webrtc.EglBase
import org.webrtc.IceCandidate
import org.webrtc.MediaConstraints
import org.webrtc.MediaStream
import org.webrtc.PeerConnection
import org.webrtc.PeerConnectionFactory
import org.webrtc.RtpTransceiver
import org.webrtc.SdpObserver
import org.webrtc.SessionDescription
import org.webrtc.SurfaceTextureHelper
import org.webrtc.VideoTrack

/**
 * Wraps PeerConnectionFactory/PeerConnection setup and camera capture behind
 * a coroutine-friendly API. One instance per call — resident (offerer, sends
 * camera+mic) and caregiver (answerer, receive-only) both use this, driven
 * by [SignalingServer]/[SignalingClient] for the actual SDP exchange.
 */
class WebRtcClient(private val context: Context) {

    val eglBase: EglBase = EglBase.create()

    private val peerConnectionFactory: PeerConnectionFactory by lazy { buildFactory() }

    private var peerConnection: PeerConnection? = null
    private var videoCapturer: CameraVideoCapturer? = null
    private var surfaceTextureHelper: SurfaceTextureHelper? = null
    private var localVideoTrack: VideoTrack? = null
    private var localAudioTrack: AudioTrack? = null
    private var iceGatheringComplete: CompletableDeferred<Unit>? = null

    private val _connectionState =
        MutableStateFlow(PeerConnection.PeerConnectionState.NEW)
    val connectionState: StateFlow<PeerConnection.PeerConnectionState> = _connectionState.asStateFlow()

    private val _remoteVideoTrack = MutableStateFlow<VideoTrack?>(null)
    val remoteVideoTrack: StateFlow<VideoTrack?> = _remoteVideoTrack.asStateFlow()

    private fun buildFactory(): PeerConnectionFactory {
        PeerConnectionFactory.initialize(
            PeerConnectionFactory.InitializationOptions.builder(context)
                .createInitializationOptions(),
        )
        val encoderFactory = DefaultVideoEncoderFactory(eglBase.eglBaseContext, true, true)
        val decoderFactory = DefaultVideoDecoderFactory(eglBase.eglBaseContext)
        return PeerConnectionFactory.builder()
            .setVideoEncoderFactory(encoderFactory)
            .setVideoDecoderFactory(decoderFactory)
            .createPeerConnectionFactory()
    }

    /** Resident side only: opens the back camera and starts a local video track. */
    fun startLocalCamera(): VideoTrack {
        val enumerator = Camera2Enumerator(context)
        val deviceName = enumerator.deviceNames.firstOrNull { enumerator.isBackFacing(it) }
            ?: enumerator.deviceNames.firstOrNull()
            ?: error("no camera available")
        val capturer = enumerator.createCapturer(deviceName, null) ?: error("no camera capturer available")
        videoCapturer = capturer

        val helper = SurfaceTextureHelper.create("WiSenseCaptureThread", eglBase.eglBaseContext)
        surfaceTextureHelper = helper

        val videoSource = peerConnectionFactory.createVideoSource(capturer.isScreencast)
        capturer.initialize(helper, context, videoSource.capturerObserver)
        capturer.startCapture(VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS)

        val track = peerConnectionFactory.createVideoTrack("wisense_video", videoSource)
        localVideoTrack = track
        return track
    }

    /**
     * Opens the mic and starts a local audio track. Used by the resident
     * (camera+mic) and, for two-way audio, the caregiver too (mic only —
     * it never calls [startLocalCamera]).
     */
    fun startLocalAudio(): AudioTrack {
        val audioSource = peerConnectionFactory.createAudioSource(MediaConstraints())
        val track = peerConnectionFactory.createAudioTrack("wisense_audio", audioSource)
        localAudioTrack = track
        return track
    }

    /**
     * [sendLocalMedia] attaches whichever local tracks were already started
     * above (addTrack is a no-op for a track that was never started) — true
     * for the resident (camera+mic) and, since two-way audio, also true for
     * the caregiver (mic only, no camera track exists). Remote video always
     * arrives via [remoteVideoTrack] regardless of this flag.
     */
    fun createPeerConnection(sendLocalMedia: Boolean) {
        // No STUN server: confirmed on-device that gathering never completed
        // within 15s waiting on stun.l.google.com (network likely blocks/
        // delays outbound UDP to it). Not needed anyway — Phase 3 is same-WiFi
        // only, so local host candidates are sufficient. STUN/TURN comes back
        // once Phase 4+ needs to work across networks (doc §6/§9).
        val rtcConfig = PeerConnection.RTCConfiguration(emptyList()).apply {
            sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
        }

        val observer = object : PeerConnection.Observer {
            override fun onSignalingChange(newState: PeerConnection.SignalingState) = Unit
            override fun onIceConnectionChange(newState: PeerConnection.IceConnectionState) = Unit
            override fun onIceConnectionReceivingChange(receiving: Boolean) = Unit
            override fun onIceGatheringChange(newState: PeerConnection.IceGatheringState) {
                if (newState == PeerConnection.IceGatheringState.COMPLETE) {
                    iceGatheringComplete?.complete(Unit)
                }
            }
            override fun onIceCandidate(candidate: IceCandidate) = Unit
            override fun onIceCandidatesRemoved(candidates: Array<out IceCandidate>) = Unit
            override fun onAddStream(stream: MediaStream) = Unit
            override fun onRemoveStream(stream: MediaStream) = Unit
            override fun onDataChannel(dataChannel: DataChannel) = Unit
            override fun onRenegotiationNeeded() = Unit
            override fun onConnectionChange(newState: PeerConnection.PeerConnectionState) {
                _connectionState.value = newState
            }
            override fun onTrack(transceiver: RtpTransceiver) {
                val track = transceiver.receiver.track()
                if (track is VideoTrack) _remoteVideoTrack.value = track
            }
        }

        val pc = peerConnectionFactory.createPeerConnection(rtcConfig, observer)
            ?: error("createPeerConnection returned null")
        peerConnection = pc

        if (sendLocalMedia) {
            localVideoTrack?.let { pc.addTrack(it, listOf(STREAM_ID)) }
            localAudioTrack?.let { pc.addTrack(it, listOf(STREAM_ID)) }
        }
    }

    /**
     * Resident side: creates the offer and suspends until ICE gathering
     * completes, returning the local description with every candidate baked
     * into the SDP text — this single-shot description is what makes
     * signaling over one manually-typed message (no trickle exchange) work.
     */
    suspend fun createOfferAndGatherIce(): String {
        val pc = peerConnection ?: error("call createPeerConnection() first")
        val offer = pc.createOfferSuspend()
        pc.setLocalDescriptionSuspend(offer)
        awaitIceGatheringComplete()
        return pc.localDescription?.description ?: error("no local description after ICE gathering")
    }

    /** Caregiver side: applies the resident's offer and returns a gathered answer. */
    suspend fun createAnswerAndGatherIce(remoteOfferSdp: String): String {
        val pc = peerConnection ?: error("call createPeerConnection() first")
        pc.setRemoteDescriptionSuspend(SessionDescription(SessionDescription.Type.OFFER, remoteOfferSdp))
        val answer = pc.createAnswerSuspend()
        pc.setLocalDescriptionSuspend(answer)
        awaitIceGatheringComplete()
        return pc.localDescription?.description ?: error("no local description after ICE gathering")
    }

    /** Resident side: applies the caregiver's answer once it arrives back over signaling. */
    suspend fun applyRemoteAnswer(remoteAnswerSdp: String) {
        val pc = peerConnection ?: error("call createPeerConnection() first")
        pc.setRemoteDescriptionSuspend(SessionDescription(SessionDescription.Type.ANSWER, remoteAnswerSdp))
    }

    private suspend fun awaitIceGatheringComplete() {
        val deferred = CompletableDeferred<Unit>()
        iceGatheringComplete = deferred
        // Gathering may already have finished (e.g. zero-candidate edge case)
        // between setLocalDescription returning and us registering the
        // deferred above — check current state directly as a fallback.
        if (peerConnection?.iceGatheringState() == PeerConnection.IceGatheringState.COMPLETE) {
            deferred.complete(Unit)
        }
        withTimeout(ICE_GATHERING_TIMEOUT_MS) { deferred.await() }
    }

    /**
     * Keeps the flash on for the whole stream, not just a moment before it.
     * There is no public API for this: [CameraVideoCapturer]/Camera2Session
     * expose no torch control at all (confirmed by inspecting the
     * stream-webrtc-android 1.3.10 classes directly), and
     * CameraManager.setTorchMode() only works when no app holds the camera
     * device open — which our own capture session does, and opening that
     * session is documented to force any prior torch-mode state off anyway.
     * The only way to keep it on *during* capture is to resubmit the
     * repeating request that's actually driving the camera with
     * FLASH_MODE_TORCH added, which means reaching into the capturer's
     * private CameraDevice/CameraCaptureSession/Surface via reflection.
     * Every step is best-effort: returns false (never throws) the moment
     * anything doesn't match what this library version's internals look
     * like, e.g. after a future stream-webrtc-android upgrade renames a
     * field — silently losing the flash is fine, silently breaking the
     * actual video/audio capture never is.
     */
    fun setTorch(enabled: Boolean): Boolean = try {
        val session = videoCapturer?.getPrivateField("currentSession")
        if (session == null || session.javaClass.simpleName != "Camera2Session") {
            false
        } else {
            val cameraDevice = session.getPrivateField("cameraDevice") as? CameraDevice
            val captureSession = session.getPrivateField("captureSession") as? CameraCaptureSession
            val surface = session.getPrivateField("surface") as? Surface
            if (cameraDevice == null || captureSession == null || surface == null) {
                false
            } else {
                val request = cameraDevice.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply {
                    addTarget(surface)
                    set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO)
                    set(
                        CaptureRequest.FLASH_MODE,
                        if (enabled) CaptureRequest.FLASH_MODE_TORCH else CaptureRequest.FLASH_MODE_OFF,
                    )
                }
                captureSession.setRepeatingRequest(request.build(), null, null)
                true
            }
        }
    } catch (e: Exception) {
        false
    }

    /**
     * Both apps use this — neither side of a call is holding the phone to
     * an ear (resident's is likely on the floor, caregiver wants to watch
     * the video while talking). On API 31+ the deprecated
     * isSpeakerphoneOn flag is unreliable on some OEM skins once a call is
     * actually routing audio, so prefer setCommunicationDevice() there and
     * only fall back to the old flag on older API levels.
     */
    fun setSpeakerphoneOn(on: Boolean) {
        val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        audioManager.mode = if (on) AudioManager.MODE_IN_COMMUNICATION else AudioManager.MODE_NORMAL
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            if (on) {
                val speaker = audioManager.availableCommunicationDevices
                    .firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
                if (speaker != null) audioManager.setCommunicationDevice(speaker)
            } else {
                audioManager.clearCommunicationDevice()
            }
        } else {
            @Suppress("DEPRECATION")
            audioManager.isSpeakerphoneOn = on
        }
    }

    fun release() {
        runCatching { videoCapturer?.stopCapture() }
        videoCapturer?.dispose()
        surfaceTextureHelper?.dispose()
        localVideoTrack?.dispose()
        localAudioTrack?.dispose()
        peerConnection?.close()
        peerConnection?.dispose()
        eglBase.release()
    }

    companion object {
        private const val STREAM_ID = "wisense_stream"
        private const val VIDEO_WIDTH = 1280
        private const val VIDEO_HEIGHT = 720
        private const val VIDEO_FPS = 30
        private const val ICE_GATHERING_TIMEOUT_MS = 15_000L
    }
}

private suspend fun PeerConnection.createOfferSuspend(): SessionDescription =
    suspendCancellableCoroutine { cont ->
        createOffer(
            object : SdpObserver {
                override fun onCreateSuccess(sdp: SessionDescription) = cont.resume(sdp)
                override fun onCreateFailure(error: String) =
                    cont.resumeWithException(IllegalStateException("createOffer failed: $error"))
                override fun onSetSuccess() = Unit
                override fun onSetFailure(error: String) = Unit
            },
            MediaConstraints(),
        )
    }

private suspend fun PeerConnection.createAnswerSuspend(): SessionDescription =
    suspendCancellableCoroutine { cont ->
        createAnswer(
            object : SdpObserver {
                override fun onCreateSuccess(sdp: SessionDescription) = cont.resume(sdp)
                override fun onCreateFailure(error: String) =
                    cont.resumeWithException(IllegalStateException("createAnswer failed: $error"))
                override fun onSetSuccess() = Unit
                override fun onSetFailure(error: String) = Unit
            },
            MediaConstraints(),
        )
    }

private suspend fun PeerConnection.setLocalDescriptionSuspend(sdp: SessionDescription) =
    suspendCancellableCoroutine<Unit> { cont ->
        setLocalDescription(
            object : SdpObserver {
                override fun onSetSuccess() = cont.resume(Unit)
                override fun onSetFailure(error: String) =
                    cont.resumeWithException(IllegalStateException("setLocalDescription failed: $error"))
                override fun onCreateSuccess(sdp: SessionDescription) = Unit
                override fun onCreateFailure(error: String) = Unit
            },
            sdp,
        )
    }

private suspend fun PeerConnection.setRemoteDescriptionSuspend(sdp: SessionDescription) =
    suspendCancellableCoroutine<Unit> { cont ->
        setRemoteDescription(
            object : SdpObserver {
                override fun onSetSuccess() = cont.resume(Unit)
                override fun onSetFailure(error: String) =
                    cont.resumeWithException(IllegalStateException("setRemoteDescription failed: $error"))
                override fun onCreateSuccess(sdp: SessionDescription) = Unit
                override fun onCreateFailure(error: String) = Unit
            },
            sdp,
        )
    }

/** Walks up the class hierarchy since a field may be declared on a superclass. */
private fun Any.getPrivateField(name: String): Any? {
    var cls: Class<*>? = javaClass
    while (cls != null) {
        try {
            return cls.getDeclaredField(name).apply { isAccessible = true }.get(this)
        } catch (e: NoSuchFieldException) {
            cls = cls.superclass
        }
    }
    return null
}
