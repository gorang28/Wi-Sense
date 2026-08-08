package com.wisense.resident.presentation.screens

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.wisense.resident.presentation.MainViewModel

/**
 * First-run setup: explains what the app does in plain language, requests
 * the permissions BLE monitoring needs, then starts the monitor service.
 * After first run it passes straight through if permissions are already held.
 */
@Composable
fun SetupScreen(
    viewModel: MainViewModel,
    onDone: () -> Unit,
) {
    val missing by viewModel.missingPermissions.collectAsStateWithLifecycle()

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) {
        viewModel.refreshPermissions()
        // Only BLE + notifications block monitoring; camera/mic are optional (§7).
        if (viewModel.missingPermissions.value.isEmpty()) {
            viewModel.startMonitoring()
            onDone()
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = "Wi-Sense monitor",
            style = MaterialTheme.typography.headlineMedium,
        )
        Spacer(Modifier.height(16.dp))
        Text(
            text = "This phone watches for the room sensor and raises the alarm " +
                "if a fall is detected. It needs Bluetooth to find and stay " +
                "connected to the sensor, notifications so the monitor keeps " +
                "running with the screen off, and camera + mic so a caregiver " +
                "can see and hear during an emergency.",
            style = MaterialTheme.typography.bodyLarge,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(32.dp))

        if (missing.isEmpty()) {
            Button(
                onClick = {
                    viewModel.startMonitoring()
                    onDone()
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Start monitoring")
            }
        } else {
            Text(
                text = "Missing: ${missing.joinToString { it.substringAfterLast('.') }}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(12.dp))
            Button(
                onClick = { permissionLauncher.launch(viewModel.allSetupPermissions().toTypedArray()) },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Grant permissions")
            }
        }
    }
}
