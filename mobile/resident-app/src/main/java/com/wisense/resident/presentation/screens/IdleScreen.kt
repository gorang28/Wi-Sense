package com.wisense.resident.presentation.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bluetooth
import androidx.compose.material.icons.automirrored.filled.BluetoothSearching
import androidx.compose.material.icons.automirrored.filled.EventNote
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.wisense.resident.domain.model.BleEvent
import com.wisense.resident.domain.model.ConnectionState
import com.wisense.resident.presentation.MainViewModel
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * The screen for >99% of the app's life (§8): monitoring status, BLE link
 * state, and the trigger event log — "ALERT received" shows here when the
 * physical fall trigger fires on the RX board (Phase 1 milestone).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun IdleScreen(
    viewModel: MainViewModel,
    onOpenSettings: () -> Unit,
) {
    val state by viewModel.connectionState.collectAsStateWithLifecycle()
    val log by viewModel.eventLog.collectAsStateWithLifecycle()

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text("Wi-Sense", fontWeight = FontWeight.Bold)
                },
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
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 20.dp),
        ) {
            Spacer(Modifier.height(4.dp))
            StatusCard(state = state, onRetry = viewModel::retryNow)

            Spacer(Modifier.height(28.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    Icons.AutoMirrored.Filled.EventNote,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(20.dp),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = "Event log",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
            }
            Spacer(Modifier.height(12.dp))

            if (log.isEmpty()) {
                Surface(
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
                    shape = MaterialTheme.shapes.medium,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(
                        text = "Nothing yet — trigger a fall on the sensor to see ALERT arrive here.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(16.dp),
                    )
                }
            } else {
                LazyColumn(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    items(log) { event -> EventRow(event) }
                }
            }
        }
    }
}

private data class StatusPresentation(
    val label: String,
    val detail: String,
    val color: Color,
    val icon: ImageVector,
)

@Composable
private fun StatusCard(state: ConnectionState, onRetry: () -> Unit) {
    val presentation = when (state) {
        is ConnectionState.Idle -> StatusPresentation(
            "Starting", "monitor service warming up",
            MaterialTheme.colorScheme.onSurfaceVariant, Icons.Filled.Sync,
        )
        is ConnectionState.Scanning -> StatusPresentation(
            "Monitoring active", "looking for the room sensor",
            MaterialTheme.colorScheme.secondary, Icons.AutoMirrored.Filled.BluetoothSearching,
        )
        is ConnectionState.Connecting -> StatusPresentation(
            "Monitoring active", "connecting to ${state.deviceAddress}",
            MaterialTheme.colorScheme.secondary, Icons.Filled.Bluetooth,
        )
        is ConnectionState.Connected -> StatusPresentation(
            "Monitoring active", "sensor connected (${state.deviceAddress})",
            MaterialTheme.colorScheme.tertiary, Icons.Filled.CheckCircle,
        )
        is ConnectionState.Retrying -> StatusPresentation(
            "Sensor disconnected", "retry ${state.attempt} in ${state.nextRetryInSeconds} s",
            Color(0xFFB8860B), Icons.Filled.Sync,
        )
        is ConnectionState.GiveUp -> StatusPresentation(
            "Sensor disconnected", "could not reconnect — check the sensor and retry",
            MaterialTheme.colorScheme.error, Icons.Filled.ErrorOutline,
        )
        is ConnectionState.Unavailable -> StatusPresentation(
            "Cannot monitor", state.reason,
            MaterialTheme.colorScheme.error, Icons.Filled.ErrorOutline,
        )
    }

    Card(
        shape = MaterialTheme.shapes.large,
        colors = CardDefaults.cardColors(
            containerColor = presentation.color.copy(alpha = 0.10f),
        ),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(20.dp),
            verticalAlignment = Alignment.Top,
        ) {
            Surface(
                shape = CircleShape,
                color = presentation.color.copy(alpha = 0.16f),
                modifier = Modifier.size(48.dp),
            ) {
                Icon(
                    presentation.icon,
                    contentDescription = null,
                    tint = presentation.color,
                    modifier = Modifier.padding(12.dp),
                )
            }
            Spacer(Modifier.width(16.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    text = presentation.label,
                    style = MaterialTheme.typography.titleLarge,
                    color = presentation.color,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    text = presentation.detail,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (state is ConnectionState.GiveUp || state is ConnectionState.Unavailable) {
                    Spacer(Modifier.height(12.dp))
                    Button(onClick = onRetry) { Text("Retry") }
                }
            }
        }
    }
}

@Composable
private fun EventRow(event: BleEvent) {
    val timeFormat = remember { SimpleDateFormat("HH:mm:ss", Locale.getDefault()) }
    val color = when (event.kind) {
        BleEvent.Kind.ALERT -> MaterialTheme.colorScheme.error
        BleEvent.Kind.CANCEL -> MaterialTheme.colorScheme.tertiary
        BleEvent.Kind.CONNECTED -> MaterialTheme.colorScheme.tertiary
        BleEvent.Kind.DISCONNECTED -> Color(0xFFB8860B)
        BleEvent.Kind.ERROR -> MaterialTheme.colorScheme.error
        BleEvent.Kind.IDLE -> MaterialTheme.colorScheme.onSurfaceVariant
    }

    Column {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 6.dp)) {
            Text(
                text = timeFormat.format(Date(event.timestampMillis)),
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.width(12.dp))
            Text(
                text = event.detail,
                style = MaterialTheme.typography.bodyMedium,
                color = color,
            )
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
    }
}
