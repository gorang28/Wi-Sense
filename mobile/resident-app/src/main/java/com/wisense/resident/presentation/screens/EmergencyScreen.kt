package com.wisense.resident.presentation.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.wisense.resident.data.emergency.EmergencyStreamState
import com.wisense.resident.presentation.MainViewModel
import kotlinx.coroutines.delay
import org.webrtc.EglBase
import org.webrtc.RendererCommon
import org.webrtc.SurfaceViewRenderer
import org.webrtc.VideoTrack

/**
 * §8 Active Emergency screen. Shown the instant ALERT is received. Local
 * camera preview, elapsed time, "caregiver has been notified" confirmation.
 *
 * No cancel button — cancellation is physical, on the ESP32 button. This
 * screen exits automatically when the service reports the session ended
 * (CANCEL received), via the nav graph's session observer.
 */
@Composable
fun EmergencyScreen(viewModel: MainViewModel) {
    val state by viewModel.emergencyState.collectAsStateWithLifecycle()
    val active = state as? EmergencyStreamState.Active

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFF3B0A0A))
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = "Fall detected",
            style = MaterialTheme.typography.headlineMedium,
            color = Color(0xFFFFB4AB),
            fontWeight = FontWeight.Bold,
        )
        Spacer(Modifier.height(4.dp))
        Text(
            text = if (active?.caregiverConnected == true) {
                "Caregiver connected and watching live"
            } else {
                "Waiting for a caregiver to connect…"
            },
            style = MaterialTheme.typography.bodyLarge,
            color = Color(0xFFFFDAD6),
        )

        Spacer(Modifier.height(16.dp))
        ElapsedTimer(startedAtMillis = active?.startedAtMillis ?: 0L)
        Spacer(Modifier.height(16.dp))

        // §7: proceed with whatever is available; camera out → explicit fallback.
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            val videoTrack by viewModel.localVideoTrack.collectAsStateWithLifecycle()
            val eglContext = viewModel.eglBaseContext
            if (active?.cameraAvailable == true && videoTrack != null && eglContext != null) {
                CameraPreview(track = videoTrack!!, eglBaseContext = eglContext)
            } else {
                CameraUnavailableFallback(micAvailable = active?.micAvailable ?: false)
            }
        }

        Spacer(Modifier.height(16.dp))
        Text(
            text = "Press the button on the room sensor to cancel.",
            style = MaterialTheme.typography.bodyMedium,
            color = Color(0xFFFFDAD6),
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun ElapsedTimer(startedAtMillis: Long) {
    // Ticks once a second; recomputes from the real start time.
    val elapsedSeconds = rememberElapsedSeconds(startedAtMillis)
    val minutes = elapsedSeconds / 60
    val seconds = elapsedSeconds % 60
    Text(
        text = "%02d:%02d".format(minutes, seconds),
        style = MaterialTheme.typography.displaySmall,
        color = Color.White,
        fontWeight = FontWeight.Bold,
    )
}

@Composable
private fun rememberElapsedSeconds(startedAtMillis: Long): Long {
    var elapsed by remember(startedAtMillis) { mutableStateOf(0L) }
    LaunchedEffect(startedAtMillis) {
        while (true) {
            elapsed = ((System.currentTimeMillis() - startedAtMillis) / 1000L)
                .coerceAtLeast(0L)
            delay(1000L)
        }
    }
    return elapsed
}

@Composable
private fun CameraPreview(track: VideoTrack, eglBaseContext: EglBase.Context) {
    var attachedRenderer by remember { mutableStateOf<SurfaceViewRenderer?>(null) }

    DisposableEffect(track) {
        onDispose {
            attachedRenderer?.let { track.removeSink(it) }
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .clip(RoundedCornerShape(16.dp))
            .background(Color.Black),
    ) {
        AndroidView(
            factory = { ctx ->
                SurfaceViewRenderer(ctx).apply {
                    init(eglBaseContext, null)
                    setScalingType(RendererCommon.ScalingType.SCALE_ASPECT_FIT)
                    setEnableHardwareScaler(true)
                    setMirror(true)
                    track.addSink(this)
                    attachedRenderer = this
                }
            },
            modifier = Modifier.fillMaxSize(),
        )
    }
}

@Composable
private fun CameraUnavailableFallback(micAvailable: Boolean) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .clip(RoundedCornerShape(16.dp))
            .background(Color(0xFF1C1B1F))
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = "Emergency detected — no camera feed",
            style = MaterialTheme.typography.titleMedium,
            color = Color.White,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = if (micAvailable) {
                "Audio is streaming. The camera is unavailable on this device."
            } else {
                "Camera and microphone are unavailable on this device."
            },
            style = MaterialTheme.typography.bodyMedium,
            color = Color(0xFFCAC4D0),
            textAlign = TextAlign.Center,
        )
    }
}
