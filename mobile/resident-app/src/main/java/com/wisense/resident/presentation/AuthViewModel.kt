package com.wisense.resident.presentation

import com.google.firebase.auth.FirebaseAuthException
import com.google.firebase.firestore.FirebaseFirestore
import com.google.firebase.firestore.SetOptions
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.wisense.resident.data.house.HouseRepository
import com.wisense.shared.firebase.AuthClient
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await
import javax.inject.Inject

sealed interface AuthUiState {
    data object SignedOut : AuthUiState
    data object Working : AuthUiState
    data class SignedIn(val houseId: String) : AuthUiState
    data class Error(val message: String) : AuthUiState
}

@HiltViewModel
class AuthViewModel @Inject constructor(
    private val houseRepository: HouseRepository,
) : ViewModel() {

    private val _state = MutableStateFlow<AuthUiState>(AuthUiState.SignedOut)
    val state: StateFlow<AuthUiState> = _state.asStateFlow()

    private val _passwordResetMessage = MutableStateFlow<String?>(null)
    val passwordResetMessage: StateFlow<String?> = _passwordResetMessage.asStateFlow()

    init {
        if (AuthClient.currentUser != null) onSignedIn()
    }

    fun signUp(email: String, password: String) = withAuth { AuthClient.signUp(email, password) }
    fun signIn(email: String, password: String) = withAuth { AuthClient.signIn(email, password) }

    /** [idToken] comes from Credential Manager's GetGoogleIdOption on the UI side. */
    fun signInWithGoogle(idToken: String) = withAuth { AuthClient.signInWithGoogleIdToken(idToken) }

    fun sendPasswordReset(email: String) {
        viewModelScope.launch {
            _passwordResetMessage.value = try {
                AuthClient.sendPasswordResetEmail(email.trim())
                "Password reset email sent — check your inbox."
            } catch (e: Exception) {
                mapAuthError(e)
            }
        }
    }

    fun clearPasswordResetMessage() {
        _passwordResetMessage.value = null
    }

    fun clearError() {
        if (_state.value is AuthUiState.Error) _state.value = AuthUiState.SignedOut
    }

    private fun withAuth(block: suspend () -> Unit) {
        _state.value = AuthUiState.Working
        viewModelScope.launch {
            try {
                block()
                onSignedIn()
            } catch (e: Exception) {
                _state.value = AuthUiState.Error(mapAuthError(e))
            }
        }
    }

    private fun onSignedIn() {
        viewModelScope.launch {
            try {
                val uid = AuthClient.currentUser?.uid ?: error("no user after sign-in")
                FirebaseFirestore.getInstance().collection("wisense_users").document(uid)
                    .set(mapOf("role" to "resident"), SetOptions.merge())
                    .await()
                val houseId = houseRepository.ensureHouse()
                _state.value = AuthUiState.SignedIn(houseId)
            } catch (e: Exception) {
                _state.value = AuthUiState.Error(mapAuthError(e))
            }
        }
    }

    /**
     * Firebase Auth intentionally merges "wrong password" and "no such user"
     * into ERROR_INVALID_CREDENTIAL on current SDK versions to avoid leaking
     * which emails are registered — so that one case reads as a combined
     * message rather than pretending we can still tell them apart.
     */
    private fun mapAuthError(e: Exception): String {
        val code = (e as? FirebaseAuthException)?.errorCode
        return when (code) {
            "ERROR_INVALID_EMAIL" -> "That email address doesn't look right."
            "ERROR_EMAIL_ALREADY_IN_USE" ->
                "An account already exists with that email — try signing in instead."
            "ERROR_WEAK_PASSWORD" -> "Password is too weak — use at least 6 characters."
            "ERROR_USER_NOT_FOUND" -> "No account found with that email."
            "ERROR_WRONG_PASSWORD" -> "Incorrect password."
            "ERROR_INVALID_CREDENTIAL" -> "Incorrect email or password."
            "ERROR_USER_DISABLED" -> "This account has been disabled."
            "ERROR_TOO_MANY_REQUESTS" -> "Too many attempts — wait a bit and try again."
            "ERROR_NETWORK_REQUEST_FAILED" -> "Network error — check your connection."
            else -> e.message ?: "Something went wrong — please try again."
        }
    }
}
