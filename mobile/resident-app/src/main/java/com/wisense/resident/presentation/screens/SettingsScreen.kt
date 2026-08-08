package com.wisense.resident.presentation.screens

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Logout
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.wisense.resident.BuildConfig
import com.wisense.resident.presentation.MainViewModel

/**
 * §8 Settings: re-pair device, notification preferences, and the
 * battery-optimization exemption path (critical on a dedicated device whose
 * only job is to keep this monitor alive in the background).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    viewModel: MainViewModel,
    onBack: () -> Unit,
    onLoggedOut: () -> Unit,
) {
    var showLogoutConfirm by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val showMonitorNotification by viewModel.showMonitorNotification.collectAsStateWithLifecycle()
    var batteryExempt by remember { mutableStateOf(context.isIgnoringBatteryOptimizations()) }
    val houseId by viewModel.houseId.collectAsStateWithLifecycle()
    val addCaregiverResult by viewModel.addCaregiverResult.collectAsStateWithLifecycle()
    var caregiverCodeInput by remember { mutableStateOf("") }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Settings") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 8.dp),
        ) {
            SectionHeader("Sensor")

            Text(
                text = "Disconnect from the current room sensor and pair again " +
                    "from scratch. Use this if the sensor was replaced or moved.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            Button(
                onClick = {
                    viewModel.rePair()
                    onBack()
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Re-pair device")
            }

            SettingsDivider()

            SectionHeader("Notifications")

            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Column(Modifier.weight(1f)) {
                    Text("Monitoring status", style = MaterialTheme.typography.bodyLarge)
                    Text(
                        text = "Show the persistent notification while the monitor is running.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Switch(
                    checked = showMonitorNotification,
                    onCheckedChange = { viewModel.setShowMonitorNotification(it) },
                )
            }

            SettingsDivider()

            SectionHeader("Battery")

            Text(
                text = "For the monitor to keep running reliably with the screen " +
                    "off, this phone should not battery-optimize Wi-Sense. This " +
                    "matters most on a dedicated device.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            if (batteryExempt) {
                Text(
                    text = "Battery optimization is off for Wi-Sense — good.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.primary,
                )
            } else {
                Button(
                    onClick = {
                        context.requestBatteryOptimizationExemption()
                        batteryExempt = context.isIgnoringBatteryOptimizations()
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("Exclude from battery optimization")
                }
            }

            SettingsDivider()

            SectionHeader("Caregivers")

            Text(
                text = "House code: ${houseId ?: "not set up yet"}",
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                text = "A caregiver signs in on their own app first, then sends you " +
                    "the 6-digit code from that app's setup screen. Enter it below to " +
                    "give them access to this house's alerts.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = caregiverCodeInput,
                onValueChange = { caregiverCodeInput = it },
                label = { Text("Caregiver's code") },
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(8.dp))
            Button(
                onClick = {
                    viewModel.addCaregiver(caregiverCodeInput.trim())
                    caregiverCodeInput = ""
                },
                enabled = caregiverCodeInput.isNotBlank(),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Add caregiver")
            }
            addCaregiverResult?.let { result ->
                Spacer(Modifier.height(8.dp))
                Text(result, style = MaterialTheme.typography.bodySmall)
            }

            SettingsDivider()

            SectionHeader("Account")

            OutlinedButton(
                onClick = { showLogoutConfirm = true },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(Icons.AutoMirrored.Filled.Logout, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.height(0.dp))
                Text("  Log out")
            }

            SettingsDivider()

            SectionHeader("About")

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text("Version", style = MaterialTheme.typography.bodyLarge)
                Text(
                    // "-debug"/"-release" build-type suffixes are implementation
                    // detail, not something a user needs to see.
                    text = BuildConfig.VERSION_NAME.substringBefore("-"),
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.height(32.dp))
        }
    }

    if (showLogoutConfirm) {
        AlertDialog(
            onDismissRequest = { showLogoutConfirm = false },
            title = { Text("Log out?") },
            text = { Text("You'll need to sign in again to keep monitoring alerts for this house.") },
            confirmButton = {
                TextButton(
                    onClick = {
                        showLogoutConfirm = false
                        viewModel.logout()
                        onLoggedOut()
                    },
                ) { Text("Log out") }
            },
            dismissButton = {
                TextButton(onClick = { showLogoutConfirm = false }) { Text("Cancel") }
            },
        )
    }
}

@Composable
private fun SectionHeader(title: String) {
    Text(
        text = title,
        style = MaterialTheme.typography.titleSmall,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(vertical = 8.dp),
    )
}

@Composable
private fun SettingsDivider() {
    HorizontalDivider(
        modifier = Modifier.padding(vertical = 16.dp),
        color = MaterialTheme.colorScheme.outlineVariant,
    )
}

private fun Context.isIgnoringBatteryOptimizations(): Boolean {
    val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
    return pm.isIgnoringBatteryOptimizations(packageName)
}

private fun Context.requestBatteryOptimizationExemption() {
    try {
        startActivity(
            Intent(
                Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                Uri.parse("package:$packageName"),
            ),
        )
    } catch (e: Exception) {
        // Some OEMs block the direct request — fall back to the list screen.
        startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
    }
}
