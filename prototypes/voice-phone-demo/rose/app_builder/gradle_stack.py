"""The one pinned Android stack ordinary Gradle projects build with (docs/APP-BUILDER.md §2.4).

Every version, archive and digest the builder, its provisioning (rose.app_builder.gradle_prepare) and the writer's
turn text (rose.app_builder.generator) name comes from here, so they cannot drift apart. Changing a pin is a
reviewed source change; the host reproduces it with ``python -m rose.app_builder.gradle_prepare``.

Builds run offline: a Gradle project may use exactly the plugins and libraries in ``PLUGINS`` and ``LIBRARIES``
(and what they depend on), resolved from the host's read-only dependency cache that provisioning seeds from them.
"""
from __future__ import annotations

from dataclasses import dataclass

GRADLE = '9.9.1'
AGP = '9.5.1'
KOTLIN = '2.4.21'
COMPOSE_BOM = '2026.09.00'
JDK_FEATURE = 21  # the JDK Gradle and AGP run on, and the Java/Kotlin toolchain a project may request

COMPILE_SDK = 37  # the current stable platform (Android 17)
PLATFORM = 'android-37.0'  # its SDK package directory (platforms;android-37.0)
BUILD_TOOLS = '36.0.0'  # AGP 9.5.1's default build tools, so a project need not name a version
# The skeleton's levels. minSdk stays where every generated app already is (Rose Agent itself needs API 29, so
# no phone that runs Rose is left out); newer APIs are used behind Build.VERSION.SDK_INT checks.
MIN_SDK = 26
TARGET_SDK = COMPILE_SDK
# What the builder accepts in the built APK, whatever the project chose: it must install on every phone Rose
# Agent runs on (API 29 and later) and on current Android, which refuses targetSdk below 24.
MAX_MIN_SDK = 29
MIN_TARGET_SDK = 24
# Test targets, not a cutoff: Android 14 (the 2024-era reference) and the current platform.
VALIDATION_APIS = (34, COMPILE_SDK)

APP_MODULE = 'app'  # the application module: app/build.gradle(.kts), the ordinary Android Studio layout


@dataclass(frozen=True)
class Archive:
    name: str
    url: str
    sha256: str


GRADLE_DISTRIBUTION = Archive(
    'gradle-9.9.1-bin.zip', 'https://services.gradle.org/distributions/gradle-9.9.1-bin.zip',
    'd8a18a7a841dd5df825d35a685fad6f2d7f12ac0c280e57b67c27b4a520ba228')

_SDK = 'https://dl.google.com/android/repository/'
# (SDK package path, directory it installs as, archive per host OS). The SHA-256s were measured on archives that
# matched the SHA-1s in Google's repository manifest (repository2-3.xml).
SDK_PACKAGES = (
    ('platforms;' + PLATFORM, 'platforms/' + PLATFORM, {
        'linux': Archive('platform-37.0_r02.zip', _SDK + 'platform-37.0_r02.zip',
                         '840b23e827f96e64aea4c89a1194aac3dc5f6bad37edb231c5c795d890330e8d'),
        'macosx': Archive('platform-37.0_r02.zip', _SDK + 'platform-37.0_r02.zip',
                          '840b23e827f96e64aea4c89a1194aac3dc5f6bad37edb231c5c795d890330e8d'),
    }),
    ('build-tools;' + BUILD_TOOLS, 'build-tools/' + BUILD_TOOLS, {
        'linux': Archive('build-tools_r36_linux.zip', _SDK + 'build-tools_r36_linux.zip',
                         '5d9ac77fb6ff43d9da518a337b4fcf8f9097113df531d99ccefe80ef7ce8250b'),
        'macosx': None,  # measured by the first macOS provisioning; see gradle_prepare --measure
    }),
)

# JDKs Gradle runs on inside the sandbox: a plain unpacked tree, never a system JDK whose files are links into /etc.
JDKS = {
    ('linux', 'x86_64'): Archive(
        'OpenJDK21U-jdk_x64_linux_hotspot_21.0.13.1_2.tar.gz',
        'https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13.1%2B2/'
        'OpenJDK21U-jdk_x64_linux_hotspot_21.0.13.1_2.tar.gz',
        'bb8a93f9ae6e7a693b2b721e85dbeff26ae1a77447985a8d7afcdd9c7cf6222e'),
    ('darwin', 'arm64'): Archive(
        'OpenJDK21U-jdk_aarch64_mac_hotspot_21.0.13.1_2.tar.gz',
        'https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13.1%2B2/'
        'OpenJDK21U-jdk_aarch64_mac_hotspot_21.0.13.1_2.tar.gz',
        '48067f2b7c958ed104a9d8a5749be7d25220b0a356b4d2d1bcfaab3e16ad0b14'),
}

# Gradle plugins a project may apply (plugin id -> version). com.android.library is for a project's own library
# modules (Home's starter design, template and theme modules are vendored into a project as such).
PLUGINS = {
    'com.android.application': AGP,
    'com.android.library': AGP,
    'org.jetbrains.kotlin.plugin.compose': KOTLIN,
    'org.jetbrains.kotlin.plugin.serialization': KOTLIN,
}

# Libraries a project may use (group:artifact -> version; None means the Compose BOM sets it). AGP 9 compiles
# Kotlin itself, so there is no separate Kotlin Android plugin.
LIBRARIES = {
    'androidx.compose:compose-bom': COMPOSE_BOM,
    'androidx.compose.ui:ui': None,
    'androidx.compose.ui:ui-graphics': None,
    'androidx.compose.ui:ui-tooling-preview': None,
    'androidx.compose.foundation:foundation': None,
    'androidx.compose.animation:animation': None,
    'androidx.compose.material3:material3': None,
    'androidx.activity:activity-compose': '1.13.0',
    'androidx.core:core-ktx': '1.19.1',
    'androidx.lifecycle:lifecycle-runtime-compose': '2.11.0',
    'androidx.lifecycle:lifecycle-viewmodel-compose': '2.11.0',
    'androidx.navigation:navigation-compose': '2.10.2',
    'androidx.datastore:datastore-preferences': '1.3.0',
    'androidx.media3:media3-exoplayer': '1.11.1',
    'androidx.media3:media3-ui-compose': '1.11.1',
    'org.jetbrains.kotlinx:kotlinx-coroutines-android': '1.11.0',
    'org.jetbrains.kotlinx:kotlinx-serialization-json': '1.13.0',
}


def summary() -> str:
    """The stack in one paragraph, for the writer's turn text."""
    plugins = ', '.join(f'{p} {v}' for p, v in PLUGINS.items())
    managed = ', '.join(c for c, v in LIBRARIES.items() if v is None)
    pinned = ', '.join(f'{c}:{v}' for c, v in LIBRARIES.items() if v is not None and not c.endswith('-bom'))
    return (f'The host builds ordinary Gradle projects with Gradle {GRADLE}, the Android Gradle Plugin {AGP} '
            f'(which compiles Kotlin {KOTLIN} itself: apply no Kotlin Android plugin), JDK {JDK_FEATURE}, '
            f'compileSdk {COMPILE_SDK}, and offline: only these plugins ({plugins}) and these libraries resolve. '
            f'From the Compose BOM androidx.compose:compose-bom:{COMPOSE_BOM}, without versions: {managed}. '
            f'With these exact versions: {pinned}.')
