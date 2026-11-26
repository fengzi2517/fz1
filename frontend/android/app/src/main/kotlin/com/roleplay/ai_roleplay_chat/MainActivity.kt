package com.roleplay.ai_roleplay_chat

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStream

/**
 * 应用入口 + 原生语音通道。
 *
 * ASR 有两种模式：
 *  1. 系统识别：SpeechRecognizer（需要系统/Google 语音服务）
 *  2. 录音上传：AudioRecord 录 16k 单声道 WAV，交给后端 whisper 识别
 *
 * Dart -> Android: isAvailable / start / stop / cancel / startRecording / stopRecording / cancelRecording
 * Android -> Dart: onResult(text, isFinal) / onLevel(level) / onError(code)
 */
class MainActivity : FlutterActivity() {
    private val channelName = "ai_roleplay/asr"
    private var speechRecognizer: SpeechRecognizer? = null
    private var channel: MethodChannel? = null

    private val permissionRequestCode = 1001

    // 录音上传模式
    private var audioRecord: AudioRecord? = null
    private var recordThread: Thread? = null
    private var recordBuffer: ByteArrayOutputStream? = null
    @Volatile
    private var isRecording = false
    private var pendingRecordResult: MethodChannel.Result? = null

    // 云端 TTS 播放（直接流式播放后端返回的 mp3）
    private var mediaPlayer: MediaPlayer? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        channel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
        channel?.setMethodCallHandler { call, result ->
            when (call.method) {
                "isAvailable" -> {
                    val available = try {
                        SpeechRecognizer.isRecognitionAvailable(this)
                    } catch (e: Exception) {
                        false
                    }
                    result.success(available)
                }

                "start" -> {
                    if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                        != PackageManager.PERMISSION_GRANTED
                    ) {
                        requestPermissions(
                            arrayOf(Manifest.permission.RECORD_AUDIO),
                            permissionRequestCode
                        )
                        result.success(false)
                    } else {
                        startListening()
                        result.success(true)
                    }
                }

                "stop" -> {
                    speechRecognizer?.stopListening()
                    result.success(true)
                }

                "cancel" -> {
                    speechRecognizer?.cancel()
                    result.success(true)
                }

                "startRecording" -> {
                    if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                        != PackageManager.PERMISSION_GRANTED
                    ) {
                        pendingRecordResult = result
                        requestPermissions(
                            arrayOf(Manifest.permission.RECORD_AUDIO),
                            permissionRequestCode
                        )
                    } else {
                        result.success(startRecording())
                    }
                }

                "stopRecording" -> result.success(stopRecording())

                "cancelRecording" -> {
                    stopRecording(delete = true)
                    result.success(true)
                }

                "deleteRecording" -> {
                    val path = call.argument<String>("path")
                    if (path != null) {
                        try {
                            File(path).delete()
                        } catch (_: Exception) {
                        }
                    }
                    result.success(true)
                }

                "playUrl" -> {
                    val url = call.argument<String>("url")
                    if (url.isNullOrEmpty()) {
                        result.success(false)
                    } else {
                        result.success(playUrl(url))
                    }
                }

                "stopUrl" -> {
                    stopPlayback()
                    result.success(true)
                }

                else -> result.notImplemented()
            }
        }
    }

    // ---------- 系统语音识别 ----------

    private fun startListening() {
        if (speechRecognizer == null) {
            speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this).apply {
                setRecognitionListener(object : RecognitionListener {
                    override fun onReadyForSpeech(params: Bundle?) {}
                    override fun onBeginningOfSpeech() {}

                    override fun onRmsChanged(rmsdB: Float) {
                        channel?.invokeMethod("onLevel", rmsdB.toDouble())
                    }

                    override fun onBufferReceived(buffer: ByteArray?) {}
                    override fun onEndOfSpeech() {}

                    override fun onError(error: Int) {
                        channel?.invokeMethod(
                            "onError",
                            mapOf("code" to error, "message" to errorText(error))
                        )
                    }

                    override fun onResults(results: Bundle?) {
                        val text = results
                            ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                            ?.firstOrNull() ?: ""
                        channel?.invokeMethod(
                            "onResult",
                            mapOf("text" to text, "isFinal" to true)
                        )
                    }

                    override fun onPartialResults(partialResults: Bundle?) {
                        val text = partialResults
                            ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                            ?.firstOrNull() ?: ""
                        if (text.isNotEmpty()) {
                            channel?.invokeMethod(
                                "onResult",
                                mapOf("text" to text, "isFinal" to false)
                            )
                        }
                    }

                    override fun onEvent(eventType: Int, params: Bundle?) {}
                })
            }
        }

        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM
            )
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        }
        try {
            speechRecognizer?.startListening(intent)
        } catch (e: Exception) {
            channel?.invokeMethod(
                "onError",
                mapOf("code" to -1, "message" to (e.message ?: "start failed"))
            )
        }
    }

    private fun errorText(code: Int): String = when (code) {
        SpeechRecognizer.ERROR_AUDIO -> "录音出错"
        SpeechRecognizer.ERROR_CLIENT -> "客户端错误"
        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "麦克风权限不足"
        SpeechRecognizer.ERROR_NETWORK -> "网络错误"
        SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "网络超时"
        SpeechRecognizer.ERROR_NO_MATCH -> "没有识别到内容"
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "识别服务忙"
        SpeechRecognizer.ERROR_SERVER -> "识别服务异常"
        SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "没有听到说话"
        else -> "识别失败($code)"
    }

    // ---------- 录音上传模式（后端 ASR） ----------

    private fun startRecording(): Boolean {
        if (isRecording) return true
        return try {
            val sampleRate = 16000
            val minBuf = AudioRecord.getMinBufferSize(
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            )
            if (minBuf <= 0) return false

            val recorder = AudioRecord(
                MediaRecorder.AudioSource.MIC,
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minBuf * 2, 4096)
            )
            if (recorder.state != AudioRecord.STATE_INITIALIZED) {
                recorder.release()
                return false
            }

            val buffer = ByteArrayOutputStream()
            recordBuffer = buffer
            audioRecord = recorder
            isRecording = true
            recorder.startRecording()
            recordThread = Thread {
                val chunk = ByteArray(2048)
                while (isRecording) {
                    val read = recorder.read(chunk, 0, chunk.size)
                    if (read > 0) {
                        buffer.write(chunk, 0, read)
                    } else if (read < 0) {
                        break
                    }
                }
            }.apply { start() }
            true
        } catch (e: Exception) {
            cleanupRecording()
            false
        }
    }

    private fun stopRecording(delete: Boolean = false): String? {
        if (!isRecording && audioRecord == null) return null
        isRecording = false
        try {
            recordThread?.join(1500)
        } catch (_: InterruptedException) {
        }
        recordThread = null
        val pcm = recordBuffer?.toByteArray() ?: ByteArray(0)
        cleanupRecording()

        if (delete || pcm.isEmpty()) return null

        return try {
            val file = File(cacheDir, "asr_${System.currentTimeMillis()}.wav")
            FileOutputStream(file).use { out ->
                writeWavHeader(out, pcm.size)
                out.write(pcm)
            }
            file.absolutePath
        } catch (e: Exception) {
            null
        }
    }

    private fun cleanupRecording() {
        try {
            audioRecord?.stop()
        } catch (_: Exception) {
        }
        try {
            audioRecord?.release()
        } catch (_: Exception) {
        }
        audioRecord = null
        recordBuffer = null
        isRecording = false
    }

    // ---------- 云端 TTS 播放 ----------

    private fun playUrl(url: String): Boolean {
        stopPlayback()
        return try {
            val player = MediaPlayer()
            mediaPlayer = player
            player.setAudioStreamType(android.media.AudioManager.STREAM_MUSIC)
            player.setDataSource(url)
            player.setOnPreparedListener { it.start() }
            player.setOnCompletionListener {
                channel?.invokeMethod("onTtsDone", null)
                stopPlayback()
            }
            player.setOnErrorListener { _, _, _ ->
                channel?.invokeMethod("onTtsDone", null)
                stopPlayback()
                true
            }
            player.prepareAsync()
            true
        } catch (e: Exception) {
            stopPlayback()
            false
        }
    }

    private fun stopPlayback() {
        try {
            mediaPlayer?.stop()
        } catch (_: Exception) {
        }
        try {
            mediaPlayer?.release()
        } catch (_: Exception) {
        }
        mediaPlayer = null
    }

    /** 写 44 字节标准 WAV 头（16k / 单声道 / 16bit） */
    private fun writeWavHeader(
        out: OutputStream,
        pcmBytes: Int,
        sampleRate: Int = 16000,
        channels: Int = 1,
        bitsPerSample: Int = 16
    ) {
        fun le32(v: Int) = byteArrayOf(
            (v and 0xff).toByte(),
            ((v shr 8) and 0xff).toByte(),
            ((v shr 16) and 0xff).toByte(),
            ((v shr 24) and 0xff).toByte()
        )
        fun le16(v: Int) = byteArrayOf(
            (v and 0xff).toByte(),
            ((v shr 8) and 0xff).toByte()
        )
        val byteRate = sampleRate * channels * bitsPerSample / 8
        out.write("RIFF".toByteArray())
        out.write(le32(36 + pcmBytes))
        out.write("WAVE".toByteArray())
        out.write("fmt ".toByteArray())
        out.write(le32(16))
        out.write(le16(1)) // PCM
        out.write(le16(channels))
        out.write(le32(sampleRate))
        out.write(le32(byteRate))
        out.write(le16(channels * bitsPerSample / 8))
        out.write(le16(bitsPerSample))
        out.write("data".toByteArray())
        out.write(le32(pcmBytes))
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != permissionRequestCode) return
        val granted = grantResults.isNotEmpty() &&
            grantResults[0] == PackageManager.PERMISSION_GRANTED

        // 录音上传模式的权限回调
        pendingRecordResult?.let { pending ->
            pendingRecordResult = null
            pending.success(if (granted) startRecording() else false)
            return
        }

        if (granted) {
            startListening()
        } else {
            channel?.invokeMethod(
                "onError",
                mapOf("code" to -2, "message" to "麦克风权限被拒绝")
            )
        }
    }

    override fun onDestroy() {
        speechRecognizer?.destroy()
        speechRecognizer = null
        stopRecording(delete = true)
        stopPlayback()
        super.onDestroy()
    }
}
