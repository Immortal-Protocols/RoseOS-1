import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.PI
import kotlin.math.exp
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.sin

plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.compose)
}

// OWNER: Player template engineer. Template: behaviour and state only. Depends on :design, never on a theme or Material.
android {
    namespace = "app.rose.template.player"
    compileSdk = libs.versions.compileSdk.get().toInt()
    defaultConfig { minSdk = libs.versions.minSdk.get().toInt() }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true }
    testOptions { unitTests { isIncludeAndroidResources = true } }
}

dependencies {
    api(project(":design"))
    implementation(libs.activity.compose)

    testImplementation(project(":design-testing"))
    testImplementation(project(":examples"))
    debugImplementation(libs.compose.ui.test.manifest)
}

val generatePlayerClip by tasks.registering(GeneratePlayerClip::class)
androidComponents.onVariants { variant ->
    variant.sources.res?.addGeneratedSourceDirectory(generatePlayerClip, GeneratePlayerClip::output)
}

/** Synthesize the original Between Stations score as mono PCM, with no external build tools. */
@CacheableTask
abstract class GeneratePlayerClip : DefaultTask() {
    @get:OutputDirectory
    abstract val output: DirectoryProperty

    @TaskAction
    fun generate() {
        val sampleRate = 22_050
        val beatLength = 0.5
        // Twelve four-beat bars, each with a bass pitch followed by four melody pitches.
        val score = listOf(
            intArrayOf(45, 57, 60, 64, 67), intArrayOf(41, 57, 60, 65, 69),
            intArrayOf(43, 55, 59, 62, 67), intArrayOf(48, 55, 60, 64, 71),
            intArrayOf(50, 57, 62, 65, 69), intArrayOf(45, 57, 60, 64, 72),
            intArrayOf(41, 53, 57, 60, 64), intArrayOf(43, 55, 59, 62, 69),
            intArrayOf(45, 57, 60, 64, 76), intArrayOf(48, 60, 64, 67, 72),
            intArrayOf(41, 53, 57, 60, 65), intArrayOf(45, 57, 60, 64, 69),
        )
        val dynamics = doubleArrayOf(0.72, 0.83, 0.92, 0.81, 0.95, 1.0, 0.83, 0.91, 0.79, 0.88, 0.72, 0.63)
        val accents = doubleArrayOf(1.0, 0.62, 0.86, 0.77)
        val samples = DoubleArray((score.size * 4 * beatLength * sampleRate).roundToInt())

        fun addNote(start: Int, pitch: Int, length: Double, volume: Double, harmonic: Double) {
            val frequency = 440 * 2.0.pow((pitch - 69) / 12.0)
            for (index in 0 until (length * sampleRate).roundToInt()) {
                val time = index.toDouble() / sampleRate
                val phase = 2 * PI * frequency * time
                val attack = (time / 0.018).coerceAtMost(1.0)
                val release = ((length - time) / 0.08).coerceAtMost(1.0)
                val envelope = attack * release * exp(-2.6 * time)
                samples[start + index] += volume * envelope * (sin(phase) + harmonic * sin(2 * phase))
            }
        }

        score.forEachIndexed { bar, pitches ->
            for (beat in 0..3) {
                val start = ((bar * 4 + beat) * beatLength * sampleRate).roundToInt()
                val volume = accents[beat] * dynamics[bar]
                val pitch = pitches[beat + 1]
                addNote(start, pitch, 0.43, 0.25 * volume, 0.22)
                if (beat % 2 == 0) {
                    addNote(start, pitches[0], 0.84, 0.25 * volume, 0.09)
                } else {
                    addNote(start, pitch + 12, 0.24, 0.08 * volume, 0.12)
                }
            }
        }

        // RIFF/WAVE header for one 16-bit PCM channel.
        val dataSize = samples.size * 2
        val wav = ByteBuffer.allocate(44 + dataSize).order(ByteOrder.LITTLE_ENDIAN)
        wav.put("RIFF".encodeToByteArray()).putInt(36 + dataSize)
        wav.put("WAVEfmt ".encodeToByteArray()).putInt(16).putShort(1).putShort(1)
        wav.putInt(sampleRate).putInt(sampleRate * 2).putShort(2).putShort(16)
        wav.put("data".encodeToByteArray()).putInt(dataSize)
        for (sample in samples) wav.putShort((Short.MAX_VALUE * sample).roundToInt().toShort())

        val clip = output.file("raw/between_stations.wav").get().asFile
        clip.parentFile.mkdirs()
        clip.writeBytes(wav.array())
    }
}
