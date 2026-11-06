package space.hashborn.cinema

import android.content.pm.PackageInfo
import android.content.pm.Signature
import android.content.pm.PackageInstaller
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.io.File
import java.net.ServerSocket
import kotlin.concurrent.thread

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[27])
@Suppress("DEPRECATION")
class AppUpdateTest {
    private fun manifest(server: String, sha: String = "a".repeat(64), size: Int = 4) = JSONObject()
        .put("versionCode",21).put("versionName","0.9.9").put("changes","Исправления")
        .put("apkUrl","$server/downloads/home-cinema-21-$sha.apk").put("sha256",sha)
        .put("size",size).put("minSdk",23)
    private fun rejected(block: () -> Unit) {
        try { block(); fail("Expected rejection") } catch (_: IllegalStateException) {} catch (_: IllegalArgumentException) {}
    }
    @Test fun manifestRejectsForeignServerRedirectPathsAndInvalidSizes() {
        val base="http://192.168.1.144:8093"
        assertEquals(21,AppUpdate.parse(manifest(base),base).versionCode.toInt())
        for (url in listOf("http://evil/downloads/x.apk", "$base/downloads/home-cinema.apk", "$base/downloads/../private", "$base/downloads/x.apk?token=secret")) {
            rejected { AppUpdate.parse(manifest(base).put("apkUrl",url),base) }
        }
        rejected { AppUpdate.parse(manifest(base).put("size",0),base) }
        rejected { AppUpdate.parse(manifest(base).put("sha256","broken"),base) }
    }
    @Test fun checksumRejectsTruncationAndCorruption() {
        val file=File.createTempFile("update", ".apk")
        try {
            file.writeText("data")
            val info=AppUpdate.parse(manifest("http://home",sha256(file)),"http://home")
            UpdateDownload.verifyDownload(file,info)
            file.writeText("nope")
            rejected {UpdateDownload.verifyDownload(file,info)}
            file.writeText("dat")
            rejected {UpdateDownload.verifyDownload(file,info)}
        } finally {file.delete()}
    }
    @Test fun packageValidationRequiresSameIdentityNewerVersionAndExistingSigningKey() {
        val context=RuntimeEnvironment.getApplication()
        val file=File(context.cacheDir,"signed.apk").apply {writeText("data")}
        val shadow=shadowOf(context.packageManager)
        fun packageInfo(code:Int, cert:String, name:String=context.packageName)=PackageInfo().apply {
            packageName=name;versionCode=code;versionName="0.9.9";signatures=arrayOf(Signature(cert.toByteArray()))
        }
        shadow.installPackage(packageInfo(20,"existing-key"))
        val info=AppUpdate.parse(manifest("http://home",sha256(file)),"http://home")
        shadow.setPackageArchiveInfo(file.absolutePath,packageInfo(21,"existing-key"))
        verifyUpdatePackage(context,file,info)
        shadow.setPackageArchiveInfo(file.absolutePath,packageInfo(21,"other-key"))
        rejected {verifyUpdatePackage(context,file,info)}
        shadow.setPackageArchiveInfo(file.absolutePath,packageInfo(21,"existing-key","another.app"))
        rejected {verifyUpdatePackage(context,file,info)}
        shadow.setPackageArchiveInfo(file.absolutePath,packageInfo(19,"existing-key"))
        rejected {verifyUpdatePackage(context,file,info)}
    }
    @Test fun truncatedNetworkDownloadNeverLeavesInstallableApk() = runBlocking {
        val server=ServerSocket(0)
        val worker=thread {
            server.accept().use { socket ->
                val reader=socket.getInputStream().bufferedReader()
                while (reader.readLine()?.isNotEmpty()==true) {}
                socket.getOutputStream().write("HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\ndat".toByteArray())
            }
        }
        try {
            val base="http://127.0.0.1:${server.localPort}"
            val info=AppUpdate.parse(manifest(base),base)
            try { UpdateDownload.download(RuntimeEnvironment.getApplication(),info) {}; fail("Truncated APK accepted") }
            catch (_: IllegalStateException) {}
            val directory=File(RuntimeEnvironment.getApplication().cacheDir,"app-update")
            assertFalse(File(directory,"update.apk").exists())
            assertFalse(File(directory,"update.part").exists())
        } finally {server.close();worker.join(2000)}
    }
    @Test fun installerErrorsExplainCancellationStorageAndSignatureWithoutDeletingData() {
        assertTrue(installationFailure(PackageInstaller.STATUS_FAILURE_ABORTED).contains("сохранены"))
        assertTrue(installationFailure(PackageInstaller.STATUS_FAILURE_STORAGE).contains("памяти"))
        assertTrue(installationFailure(PackageInstaller.STATUS_FAILURE_CONFLICT).contains("подписи"))
    }
}
