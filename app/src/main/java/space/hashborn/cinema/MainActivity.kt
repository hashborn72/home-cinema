package space.hashborn.cinema

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.*
import kotlinx.coroutines.*
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

class MainActivity : ComponentActivity() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val prefs by lazy { getSharedPreferences("cinema", MODE_PRIVATE) }
    private var busy by mutableStateOf(false)
    private var status by mutableStateOf("Подключаемся к тестовому backend…")
    private var progress by mutableStateOf<Long?>(null)
    private var lastResult by mutableStateOf("Результатов ещё нет. Запуск видео не означает просмотр.")
    private var mediaUrl = ""
    private var showProbe by mutableStateOf(false)
    private val launcher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val session = prefs.getString("active_session", null)
        val data = result.data
        val position = if (data?.hasExtra("position") == true) data.getIntExtra("position", -1).toLong() else null
        val duration = if (data?.hasExtra("duration") == true) data.getIntExtra("duration", -1).toLong() else null
        val parsed = PlaybackResult.validated(position, duration, data?.getStringExtra("end_by"))
        lastResult = "Возврат: ${if (result.resultCode == Activity.RESULT_OK) "OK" else "без результата"}; " +
            "позиция: ${parsed.positionMs?.let(::formatTime) ?: "не передана"}; причина: ${parsed.endBy ?: "не передана"}"
        if (session != null) {
            val event = JSONObject().put("session_id", session).put("event_id", UUID.randomUUID().toString())
                .put("result_ok", result.resultCode == Activity.RESULT_OK)
                .put("position_ms", parsed.positionMs ?: JSONObject.NULL)
                .put("duration_ms", parsed.durationMs ?: JSONObject.NULL)
                .put("end_by", parsed.endBy ?: JSONObject.NULL)
            // Persist before making the request: a network outage must not lose returned progress.
            prefs.edit().putString("pending_result", event.toString()).remove("active_session")
                .putString("last_result", lastResult).commit()
            refresh()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Provisioned only by the development harness; no server credential in the APK.
        if (intent.hasExtra("backend_token")) {
            prefs.edit().putString("token", intent.getStringExtra("backend_token"))
                .putString("backend", intent.getStringExtra("backend") ?: "http://192.168.0.221:18093").commit()
            intent.removeExtra("backend_token")
        }
        lastResult = prefs.getString("last_result", lastResult) ?: lastResult
        setContent {
            if (!showProbe) {
                CatalogScreen(request = { path -> request(path) },
                    post = { path, body -> request(path,body) },
                    onPlay = { release, file, resume -> playFile(release,file,resume) },
                    playerStatus = status, playerBusy = busy, onProbe = { showProbe = true })
            } else {
            BackHandler { showProbe = false }
            MaterialTheme {
                LazyColumn(modifier = Modifier.fillMaxSize().background(Color(0xFF101722)).padding(horizontal = 48.dp, vertical = 30.dp), verticalArrangement = Arrangement.spacedBy(18.dp)) {
                    item { Text("ДОМАШНЯЯ МЕДИАТЕКА", color = Color(0xFF5EEAD4), fontSize = 14.sp) }
                    item { Text("Проверка Just Player", color = Color.White, fontSize = 30.sp) }
                    item { Text("Этап 1 · Android TV · 0.1.0-probe", color = Color(0xFF9CA3AF)) }
                    item { Text(status, color = Color.White) }
                    item { Text("Сохранено на сервере: ${progress?.let(::formatTime) ?: "позиция неизвестна"}", color = Color.White, fontSize = 20.sp) }
                    item {
                        Row(horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                            Button(onClick = { play(0) }, enabled = !busy && mediaUrl.isNotEmpty()) { Text("С начала") }
                            Button(onClick = { play(42_000) }, enabled = !busy && mediaUrl.isNotEmpty()) { Text("Тест с 00:42") }
                            Button(onClick = { play(null) }, enabled = !busy && progress != null && mediaUrl.isNotEmpty()) { Text("Продолжить") }
                        }
                    }
                    item { Button(onClick = { refresh() }, enabled = !busy) { Text("Обновить с сервера") } }
                    item { Text(lastResult, color = Color(0xFFCBD5E1)) }
                    item { Text("Тестовый ролик: Big Buck Bunny · Blender Foundation (CC BY 3.0).\nBack проверяет возврат позиции. Home не считается завершением просмотра.\nJackett, TorrServer и рабочая медиатека не изменяются.", color = Color(0xFF9CA3AF), fontSize = 14.sp) }
                }
            }
            }
        }
        refresh()
    }

    private suspend fun request(path: String, body: JSONObject? = null): JSONObject = withContext(Dispatchers.IO) {
        val base = prefs.getString("backend", "http://192.168.0.221:18093")!!
        val token = prefs.getString("token", "")!!
        if (token.isEmpty()) throw IllegalStateException("Устройство ещё не подключено к backend")
        val conn = URL(base + path).openConnection() as HttpURLConnection
        try {
            conn.connectTimeout = 6000; conn.readTimeout = 6000
            conn.setRequestProperty("Authorization", "Bearer $token")
            if (body != null) {
                conn.requestMethod = "POST"; conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            if (conn.responseCode !in 200..299) throw IllegalStateException("Backend: HTTP ${conn.responseCode}")
            JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
        } finally { conn.disconnect() }
    }

    private suspend fun syncPending() {
        val raw = prefs.getString("pending_result", null) ?: return
        val event = JSONObject(raw)
        request("/api/v1/playback/sessions/${event.getString("session_id")}/result", event)
        prefs.edit().remove("pending_result").commit()
    }

    private suspend fun playFile(release: String, file: Int, resume: Boolean) {
        if (busy) throw IllegalStateException("Дождись сохранения результата плеера")
        busy = true
        try {
            syncPending()
            val session = request("/api/v1/playback/file-sessions",JSONObject()
                .put("release_id",release).put("file_id",file).put("resume",resume))
            val url = session.getString("stream_url")
            prefs.edit().putString("active_session",session.getString("id")).commit()
            launcher.launch(Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(Uri.parse(url),"video/*")
                setPackage("com.brouken.player")
                putExtra("position",session.getInt("start_position_ms"))
                putExtra("return_result",true)
                putExtra("title",session.getString("title"))
            })
            status = "Запущен Just Player. Back возвращает подтверждённую позицию."
        } catch (_: ActivityNotFoundException) {
            prefs.edit().remove("active_session").commit()
            throw IllegalStateException("Just Player не установлен")
        } finally { busy=false }
    }

    private fun refresh() {
        if (busy) return
        scope.launch {
            busy = true
            try {
                syncPending()
                val info = request("/api/v1/probe")
                mediaUrl = info.getString("stream_url")
                progress = if (info.isNull("position_ms")) null else info.getLong("position_ms")
                status = if(showProbe) "Backend доступен. Выбери режим запуска и вернись кнопкой Back." else "Синхронизация выполнена. $lastResult"
            } catch (e: Exception) {
                status = "Нет связи: ${e.message}. Полученный результат сохранён на устройстве; нажми «Обновить»."
            } finally { busy = false }
        }
    }

    private fun play(requestedPosition: Long?) {
        if (busy) return
        scope.launch {
            busy = true
            try {
                syncPending()
                // Retry may have advanced server progress since this screen was loaded.
                // Resume must use that confirmed value, not the stale button snapshot.
                val position = requestedPosition ?: run {
                    val info = request("/api/v1/probe")
                    mediaUrl = info.getString("stream_url")
                    progress = if (info.isNull("position_ms")) null else info.getLong("position_ms")
                    progress ?: 0L
                }
                val session = request("/api/v1/playback/sessions", JSONObject().put("start_position_ms", position))
                prefs.edit().putString("active_session", session.getString("id")).commit()
                val intent = Intent(Intent.ACTION_VIEW, Uri.parse(mediaUrl)).apply {
                    setDataAndType(Uri.parse(mediaUrl), "video/mp4")
                    setPackage("com.brouken.player")
                    putExtra("position", position.toInt())
                    putExtra("return_result", true)
                    putExtra("title", "Big Buck Bunny — проверка медиатеки")
                }
                launcher.launch(intent)
            } catch (_: ActivityNotFoundException) {
                prefs.edit().remove("active_session").commit()
                status = "Just Player не установлен (com.brouken.player)."
            } catch (e: Exception) { status = "Запуск не удался: ${e.message}" }
            finally { busy = false }
        }
    }

    override fun onDestroy() { scope.cancel(); super.onDestroy() }
    private fun formatTime(ms: Long): String = "%02d:%02d".format(ms / 60000, ms / 1000 % 60)
}
