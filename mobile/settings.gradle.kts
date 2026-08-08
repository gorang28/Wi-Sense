pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "wi-sense-mobile"

include(":resident-app")
include(":caregiver-app")
include(":shared:ble-protocol")
include(":shared:webrtc-core")
