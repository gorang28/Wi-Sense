package com.wisense.caregiver.presentation

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.NotificationsActive
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import org.webrtc.EglBase
import org.webrtc.RendererCommon
import org.webrtc.SurfaceViewRenderer
import org.webrtc.VideoTrack

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ViewerScreen(houseId: String, onOpenSettings: () -> Unit) {
    val application = LocalContext.current.applicationContext as android.app.Application
    val viewModel: ViewerViewModel = viewModel(
        factory = ViewModelProvider.AndroidViewModelFactory.getInstance(application),
    )
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()

    LaunchedEffect(houseId) {
        viewModel.startListening(houseId)
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Wi-Sense Caregiver", fontWeight = FontWeight.Bold) },
                actions = {
                    IconButton(onClick = onOpenSettings) {
                        Icon(Icons.Filled.Settings, contentDescription = "Settings")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
    ) { padding ->
        val streamingState = uiState as? ViewerUiState.Streaming
        if (streamingState != null) {
            Box(modifier = Modifier.fillMaxSize().padding(padding)) {
                RemoteVideoView(track = streamingState.videoTrack, eglBaseContext = viewModel.eglBaseContext)
            }
        } else {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Spacer(Modifier.height(48.dp))
                WaitingState(uiState)
            }
        }
    }
}

@Composable
private fun WaitingState(uiState: ViewerUiState) {
    val (icon, label, detail, color) = when (uiState) {
        ViewerUiState.Waiting -> StatePresentation(
            Icons.Filled.NotificationsActive,
            "Watching for alerts",
            "You'll see live video here the instant a fall is detected.",
            MaterialTheme.colorScheme.secondary,
        )
        ViewerUiState.Negotiating -> StatePresentation(
            Icons.Filled.Sync,
            "Alert received",
            "Connecting to the live feed…",
            MaterialTheme.colorScheme.tertiary,
        )
        is ViewerUiState.Error -> StatePresentation(
            Icons.Filled.ErrorOutline,
            "Something went wrong",
            uiState.message,
            MaterialTheme.colorScheme.error,
        )
        is ViewerUiState.Streaming -> return
    }

    Surface(
        shape = CircleShape,
        color = color.copy(alpha = 0.14f),
        modifier = Modifier.size(72.dp),
    ) {
        Box(contentAlignment = Alignment.Center) {
            if (uiState == ViewerUiState.Negotiating) {
                CircularProgressIndicator(color = color, modifier = Modifier.size(32.dp))
            } else {
                Icon(icon, contentDescription = null, tint = color, modifier = Modifier.size(32.dp))
            }
        }
    }
    Spacer(Modifier.height(20.dp))
    Text(label, style = MaterialTheme.typography.titleLarge, color = color, fontWeight = FontWeight.SemiBold)
    Spacer(Modifier.height(8.dp))
    Text(
        detail,
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

private data class StatePresentation(
    val icon: ImageVector,
    val label: String,
    val detail: String,
    val color: Color,
)

@Composable
private fun RemoteVideoView(track: VideoTrack, eglBaseContext: EglBase.Context?) {
    var attachedRenderer by remember { mutableStateOf<SurfaceViewRenderer?>(null) }

    DisposableEffect(track) {
        onDispose {
            attachedRenderer?.let { track.removeSink(it) }
        }
    }

    if (eglBaseContext == null) return

    AndroidView(
        factory = { ctx ->
            SurfaceViewRenderer(ctx).apply {
                init(eglBaseContext, null)
                setScalingType(RendererCommon.ScalingType.SCALE_ASPECT_FIT)
                setEnableHardwareScaler(true)
                track.addSink(this)
                attachedRenderer = this
            }
        },
        modifier = Modifier.fillMaxSize(),
    )
}
