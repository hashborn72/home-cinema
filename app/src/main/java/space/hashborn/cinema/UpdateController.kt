package space.hashborn.cinema

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.compose.runtime.*
import kotlinx.coroutines.*
import org.json.JSONObject
import java.io.File

internal class UpdateController(private val activity: ComponentActivity, private val scope: CoroutineScope,
    private val server: () -> String, private val requestPermission: (Intent) -> Unit) {
    var available by mutableStateOf<AppUpdate?>(null)
        private set
    var showDialog by mutableStateOf(false)
    var showNotice by mutableStateOf(false)
    var working by mutableStateOf(false)
        private set
    var message by mutableStateOf("")
        private set
    private val prefs = activity.getSharedPreferences("cinema_updates", 0)

    private val listener = android.content.SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
        if (key == "message") scope.launch {
            val status = prefs.getString("message", "").orEmpty()
            if (status.isNotEmpty()) { message = status; showNotice = true }
        }
    }
    init { prefs.registerOnSharedPreferenceChangeListener(listener) }
    fun close() { prefs.unregisterOnSharedPreferenceChangeListener(listener) }

    fun check(manual: Boolean) {
        if (working) return
        scope.launch {
            working = true
            if (manual) message = "Проверяем обновления…"
            try {
                val update = UpdateDownload.check(server())
                if (update.versionCode > BuildConfig.VERSION_CODE) {
                    available = update
                    message = "Доступна версия ${update.versionName}."
                    showDialog = true
                } else {
                    available = null
                    if (manual) { message = "Установлена последняя версия: ${BuildConfig.VERSION_NAME}."; showNotice = true }
                }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { if (manual) { message = error.message ?: "Не удалось проверить обновления."; showNotice = true } }
            finally { working = false }
        }
    }

    fun update() {
        if (working) return
        val update = available ?: return
        showDialog = false
        showNotice = false
        scope.launch {
            working = true
            try {
                message = "Загрузка обновления: 0%"
                val file = UpdateDownload.download(activity, update) { percent ->
                    scope.launch { message = "Загрузка обновления: $percent%" }
                }
                withContext(Dispatchers.IO) { verifyUpdatePackage(activity, file, update) }
                val saved = JSONObject().put("server", server()).put("versionCode", update.versionCode)
                    .put("versionName", update.versionName).put("changes", update.changes)
                    .put("apkUrl", update.apkUrl).put("size", update.size).put("sha256", update.sha256)
                    .put("minSdk", update.minSdk)
                withContext(Dispatchers.IO) {
                    File(file.parentFile, "ready.json").writeText(saved.toString())
                }
                if (Build.VERSION.SDK_INT >= 26 && !activity.packageManager.canRequestPackageInstalls()) {
                    prefs.edit().putBoolean("awaiting_permission", true).commit()
                    message = "Разрешите установку обновлений из Home Cinema и вернитесь кнопкой Back."
                    try {
                        requestPermission(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                            Uri.parse("package:${activity.packageName}")))
                    } catch (_: Exception) {
                        prefs.edit().remove("awaiting_permission").apply()
                        message = "Откройте настройки Android → Безопасность → Установка неизвестных приложений и разрешите Home Cinema. Затем повторите обновление."
                    }
                } else installReady()
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { message = error.message ?: "Не удалось подготовить обновление."; showNotice = true }
            finally { working = false }
        }
    }

    fun permissionReturned() {
        if (!prefs.getBoolean("awaiting_permission", false) || working) return
        prefs.edit().remove("awaiting_permission").commit()
        if (Build.VERSION.SDK_INT >= 26 && !activity.packageManager.canRequestPackageInstalls()) {
            message = "Разрешение на установку не выдано. Текущая версия и данные сохранены."
            showNotice = true
            return
        }
        scope.launch {
            working = true
            try { installReady() }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { message = error.message ?: "Не удалось запустить установку."; showNotice = true }
            finally { working = false }
        }
    }

    private suspend fun installReady() {
        withContext(Dispatchers.IO) {
            val directory = File(activity.cacheDir, "app-update")
            val json = JSONObject(File(directory, "ready.json").readText())
            check(json.getString("server") == server()) { "Адрес сервера изменился. Проверьте обновления заново." }
            val update = AppUpdate.parse(json, server())
            UpdateInstaller.install(activity, File(directory, "update.apk"), update)
        }
        message = "Подтвердите обновление в системном окне Android."
        showNotice = true
    }

    fun refreshInstallStatus() {
        val saved = prefs.getString("message", "").orEmpty()
        if (saved.isNotEmpty()) {
            message = saved
            prefs.edit().remove("message").apply()
        }
        val target = prefs.getLong("target_version", 0)
        if (target > 0 && BuildConfig.VERSION_CODE >= target) {
            message = "Обновление до ${BuildConfig.VERSION_NAME} установлено. Пользовательские данные сохранены."
            prefs.edit().remove("target_version").remove("awaiting_permission").apply()
        }
    }
}
