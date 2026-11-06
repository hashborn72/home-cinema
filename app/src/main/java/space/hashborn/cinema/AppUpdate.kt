package space.hashborn.cinema

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

internal data class AppUpdate(val versionCode: Long, val versionName: String, val changes: String,
    val apkUrl: String, val size: Long, val sha256: String, val minSdk: Int) {
    companion object {
        fun parse(json: JSONObject, server: String): AppUpdate {
            val base = URL(server)
            val url = URL(json.getString("apkUrl"))
            val code = json.getLong("versionCode")
            val size = json.getLong("size")
            val sha = json.getString("sha256")
            val minSdk = json.getInt("minSdk")
            val name = json.getString("versionName")
            val notes = json.getString("changes")
            require(base.protocol in listOf("http", "https") && base.userInfo == null &&
                url.protocol == base.protocol && url.host == base.host && url.port == base.port &&
                url.userInfo == null && url.query == null && url.ref == null &&
                code > 0 && size in 1..268_435_456L && sha.matches(Regex("[a-f0-9]{64}")) &&
                minSdk >= 23 && name.length in 1..80 && notes.length <= 12000 &&
                url.path == "/downloads/home-cinema-$code-$sha.apk") { "Некорректное описание обновления на сервере." }
            return AppUpdate(code, name, notes, url.toString(), size, sha, minSdk)
        }
    }
}

internal fun sha256(file: File): String {
    val digest = MessageDigest.getInstance("SHA-256")
    file.inputStream().use { input ->
        val buffer = ByteArray(64 * 1024)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            digest.update(buffer, 0, count)
        }
    }
    return digest.digest().joinToString("") { "%02x".format(it) }
}

internal object UpdateDownload {
    suspend fun check(server: String): AppUpdate = withContext(Dispatchers.IO) {
        val connection = URL(server.trimEnd('/') + "/api/v1/app-update").openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 6000; connection.readTimeout = 10000
            connection.instanceFollowRedirects = false
            check(connection.responseCode == 200) { "Сервер пока не опубликовал обновление (HTTP ${connection.responseCode})." }
            val bytes = connection.inputStream.use { it.readBytesLimited(32768) }
            AppUpdate.parse(JSONObject(String(bytes, Charsets.UTF_8)), server)
        } catch (e: IOException) {
            throw IllegalStateException("Нет связи с сервером обновлений. Проверьте сеть и повторите проверку.", e)
        } finally { connection.disconnect() }
    }

    suspend fun download(context: Context, info: AppUpdate, progress: (Int) -> Unit): File = withContext(Dispatchers.IO) {
        check(info.minSdk <= Build.VERSION.SDK_INT) { "Для этой версии требуется более новая версия Android." }
        val directory = File(context.cacheDir, "app-update").apply { mkdirs() }
        val target = File(directory, "update.apk")
        val partial = File(directory, "update.part")
        target.delete(); partial.delete()
        check(directory.usableSpace >= info.size * 3 + 16L * 1024 * 1024) {
            "Недостаточно свободного места. Освободите память телевизора и повторите загрузку."
        }
        val connection = URL(info.apkUrl).openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 8000; connection.readTimeout = 20000
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("Accept-Encoding", "identity")
            check(connection.responseCode == 200) { "Не удалось скачать APK (HTTP ${connection.responseCode})." }
            val contentLength = connection.getHeaderField("Content-Length")?.toLongOrNull()
            check(contentLength == null || contentLength == info.size) { "Размер APK на сервере изменился. Проверьте обновления заново." }
            check(connection.contentEncoding == null || connection.contentEncoding == "identity") { "Неверный формат загрузки APK." }
            var received = 0L
            var reported = -1
            connection.inputStream.use { input ->
                partial.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        received += count
                        check(received <= info.size) { "APK больше объявленного размера. Загрузка остановлена." }
                        output.write(buffer, 0, count)
                        val percent = (received * 100 / info.size).toInt()
                        if (percent != reported) { reported = percent; progress(percent) }
                    }
                }
            }
            verifyDownload(partial, info)
            check(partial.renameTo(target)) { "Не удалось сохранить APK. Проверьте свободное место." }
            target
        } catch (e: IOException) {
            throw IllegalStateException("Загрузка прервана: сеть недоступна или недостаточно места. Повторите попытку.", e)
        } finally {
            connection.disconnect(); partial.delete()
        }
    }

    fun verifyDownload(file: File, info: AppUpdate) {
        check(file.length() == info.size) { "APK загружен не полностью. Повторите загрузку." }
        check(sha256(file) == info.sha256) { "SHA-256 не совпадает. APK повреждён; установка отменена." }
    }
}

private fun java.io.InputStream.readBytesLimited(limit: Int): ByteArray {
    val output = java.io.ByteArrayOutputStream()
    val buffer = ByteArray(4096)
    while (true) {
        val count = read(buffer)
        if (count < 0) break
        check(output.size() + count <= limit) { "Некорректное описание обновления." }
        output.write(buffer, 0, count)
    }
    return output.toByteArray()
}

@Suppress("DEPRECATION")
internal fun verifyUpdatePackage(context: Context, apk: File, update: AppUpdate) {
    UpdateDownload.verifyDownload(apk, update)
    val pm = context.packageManager
    val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
    val archive = pm.getPackageArchiveInfo(apk.absolutePath, flags)
        ?: error("Android не смог прочитать APK. Установка отменена.")
    val current = pm.getPackageInfo(context.packageName, flags)
    fun code(p: android.content.pm.PackageInfo) = if (Build.VERSION.SDK_INT >= 28) p.longVersionCode else p.versionCode.toLong()
    check(archive.packageName == context.packageName && archive.versionName == update.versionName && code(archive) == update.versionCode &&
        code(archive) > code(current)) { "Этот APK не является новой версией Home Cinema." }
    fun signatures(p: android.content.pm.PackageInfo): Set<String> {
        val certs = if (Build.VERSION.SDK_INT >= 28) p.signingInfo?.apkContentsSigners else p.signatures
        return certs.orEmpty().map { it.toCharsString() }.toSet()
    }
    val expected = signatures(current)
    check(expected.isNotEmpty() && signatures(archive) == expected) {
        "Подпись APK не совпадает с установленным приложением. Установка отменена; данные сохранены."
    }
}
