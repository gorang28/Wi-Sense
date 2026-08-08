package com.wisense.caregiver

import android.Manifest
import android.app.NotificationManager
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import com.wisense.caregiver.data.SessionStore
import com.wisense.caregiver.presentation.AuthScreen
import com.wisense.caregiver.presentation.HouseLinkScreen
import com.wisense.caregiver.presentation.SettingsScreen
import com.wisense.caregiver.presentation.ViewerScreen
import com.wisense.caregiver.presentation.theme.WiSenseTheme
import com.wisense.shared.firebase.AuthClient

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            registerForActivityResult(ActivityResultContracts.RequestPermission()) {}
                .launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        // For two-way audio: without this, startLocalAudio() in negotiate()
        // silently captures no mic input rather than crashing, so a denial
        // here just means the caregiver can't be heard, not a hard failure.
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) !=
            android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            registerForActivityResult(ActivityResultContracts.RequestPermission()) {}
                .launch(Manifest.permission.RECORD_AUDIO)
        }

        // Android 14+ added a second gate on top of the USE_FULL_SCREEN_INTENT
        // manifest permission: the user has to explicitly grant it per-app in
        // Settings, or the alert silently degrades to a normal heads-up
        // notification, only a Settings deep link, no runtime prompt for it.
        if (Build.VERSION.SDK_INT >= 34) {
            val notificationManager = getSystemService(NotificationManager::class.java)
            if (!notificationManager.canUseFullScreenIntent()) {
                runCatching {
                    startActivity(
                        Intent(
                            Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT,
                            Uri.parse("package:$packageName"),
                        ),
                    )
                }
            }
        }

        setContent {
            WiSenseTheme {
                Surface {
                    CaregiverApp()
                }
            }
        }
    }
}

private enum class Screen { Auth, HouseLink, Viewer, Settings }

@Composable
private fun CaregiverApp() {
    val context = LocalContext.current
    val sessionStore = remember { SessionStore(context) }

    fun currentScreen() = when {
        AuthClient.currentUser == null -> Screen.Auth
        sessionStore.houseId == null -> Screen.HouseLink
        else -> Screen.Viewer
    }

    var screen by remember { mutableStateOf(currentScreen()) }

    when (screen) {
        Screen.Auth -> AuthScreen(onSignedIn = { screen = currentScreen() })
        Screen.HouseLink -> HouseLinkScreen(onLinked = { screen = currentScreen() })
        Screen.Viewer -> ViewerScreen(
            houseId = sessionStore.houseId!!,
            onOpenSettings = { screen = Screen.Settings },
        )
        Screen.Settings -> SettingsScreen(
            onBack = { screen = currentScreen() },
            onLoggedOut = { screen = currentScreen() },
        )
    }
}
