package com.wisense.resident.di

import android.bluetooth.BluetoothManager
import android.content.Context
import com.wisense.resident.data.ble.BleConnectionManager
import com.wisense.resident.data.ble.BleRepository
import com.wisense.resident.data.ble.BleRepositoryImpl
import com.wisense.resident.data.emergency.EmergencyStreamController
import com.wisense.resident.data.settings.SettingsStore
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
abstract class RepositoryModule {

    @Binds
    @Singleton
    abstract fun bindBleRepository(impl: BleRepositoryImpl): BleRepository
}

@Module
@InstallIn(SingletonComponent::class)
object AppModule {

    @Provides
    @Singleton
    fun provideBluetoothManager(@ApplicationContext context: Context): BluetoothManager =
        context.getSystemService(BluetoothManager::class.java)

    @Provides
    @Singleton
    fun provideBleConnectionManager(
        @ApplicationContext context: Context,
        bluetoothManager: BluetoothManager,
    ): BleConnectionManager = BleConnectionManager(context, bluetoothManager)

    @Provides
    @Singleton
    fun provideEmergencyStreamController(
        @ApplicationContext context: Context,
        settingsStore: SettingsStore,
    ): EmergencyStreamController = EmergencyStreamController(context, settingsStore)
}
