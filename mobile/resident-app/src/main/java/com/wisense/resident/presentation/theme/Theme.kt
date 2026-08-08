package com.wisense.resident.presentation.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

private val LightColors = lightColorScheme(
    primary = Color(0xFF1E3A5F),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFD3E4F5),
    onPrimaryContainer = Color(0xFF0B1D33),
    secondary = Color(0xFF4A7BA6),
    onSecondary = Color.White,
    tertiary = Color(0xFF2E7D32),
    onTertiary = Color.White,
    background = Color(0xFFF7F9FC),
    surface = Color(0xFFF7F9FC),
    surfaceVariant = Color(0xFFE3E9F0),
    error = Color(0xFFB3261E),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF9CC3E5),
    onPrimary = Color(0xFF0B1D33),
    primaryContainer = Color(0xFF2A4A6E),
    onPrimaryContainer = Color(0xFFD3E4F5),
    secondary = Color(0xFF8FB6D9),
    onSecondary = Color(0xFF0B1D33),
    tertiary = Color(0xFF8FCB92),
    onTertiary = Color(0xFF0B2E0E),
    background = Color(0xFF10151C),
    surface = Color(0xFF10151C),
    surfaceVariant = Color(0xFF2A323D),
    error = Color(0xFFF2B8B5),
)

private val WiSenseShapes = Shapes(
    small = RoundedCornerShape(8.dp),
    medium = RoundedCornerShape(16.dp),
    large = RoundedCornerShape(24.dp),
)

@Composable
fun WiSenseTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        shapes = WiSenseShapes,
        content = content,
    )
}
