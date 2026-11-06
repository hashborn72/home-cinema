package space.hashborn.cinema

import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import java.io.File

internal object UpdateInstaller {
    const val ACTION = "space.hashborn.cinema.UPDATE_STATUS"
    fun install(context: Context, apk: File, info: AppUpdate) {
        verifyUpdatePackage(context, apk, info)
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
            setSize(apk.length())
            if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_REQUIRED)
        }
        val id = installer.createSession(params)
        try {
            installer.openSession(id).use { session ->
                session.openWrite("base.apk", 0, apk.length()).use { output ->
                    apk.inputStream().use { it.copyTo(output) }
                    session.fsync(output)
                }
                val callback = Intent(context, UpdateInstallReceiver::class.java).setAction(ACTION)
                val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                    (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
                context.getSharedPreferences("cinema_updates", Context.MODE_PRIVATE).edit()
                    .putLong("target_version", info.versionCode).putString("message", "Ожидается подтверждение установки в Android.").commit()
                session.commit(PendingIntent.getBroadcast(context, id, callback, flags).intentSender)
            }
        } catch (error: Exception) {
            installer.abandonSession(id)
            throw error
        }
    }
}

class UpdateInstallReceiver : BroadcastReceiver() {
    @Suppress("DEPRECATION")
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != UpdateInstaller.ACTION) return
        val prefs = context.getSharedPreferences("cinema_updates", Context.MODE_PRIVATE)
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val confirmation = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                if (confirmation != null) {
                    try {
                        context.startActivity(confirmation.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    } catch (_: Exception) {
                        prefs.edit().putString("message", "Не удалось открыть подтверждение Android. Повторите установку из настроек.").apply()
                    }
                }
            }
            PackageInstaller.STATUS_SUCCESS -> {
                prefs.edit().putString("message", "Обновление установлено. Пользовательские данные сохранены.").apply()
                File(context.cacheDir, "app-update").deleteRecursively()
            }
            else -> prefs.edit().putString("message", installationFailure(intent.getIntExtra(
                PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE))).apply()
        }
    }
}

internal fun installationFailure(status: Int): String = when (status) {
    PackageInstaller.STATUS_FAILURE_ABORTED -> "Установка отменена. Текущая версия и данные сохранены."
    PackageInstaller.STATUS_FAILURE_STORAGE -> "Недостаточно памяти для установки. Освободите место и повторите."
    PackageInstaller.STATUS_FAILURE_CONFLICT -> "Конфликт пакета или подписи. Текущая версия и данные сохранены."
    PackageInstaller.STATUS_FAILURE_INCOMPATIBLE -> "Обновление несовместимо с этой версией Android TV."
    PackageInstaller.STATUS_FAILURE_BLOCKED -> "Android заблокировал установку. Разрешите установку из Home Cinema в настройках телевизора."
    PackageInstaller.STATUS_FAILURE_INVALID -> "Android отклонил APK: файл или подпись недействительны. Повторите загрузку."
    else -> "Android не смог установить обновление. Текущая версия и данные сохранены; повторите попытку."
}
