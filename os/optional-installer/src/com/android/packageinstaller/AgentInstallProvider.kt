package com.android.packageinstaller

import android.app.KeyguardManager
import android.app.admin.DevicePolicyManager
import android.content.ContentProvider
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.database.Cursor
import android.net.Uri
import android.os.Binder
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.os.SystemClock
import android.os.UserManager
import android.provider.Settings
import android.view.View
import android.widget.CheckBox
import com.android.packageinstaller.v2.model.*
import com.android.packageinstaller.v2.ui.SpecialPermissionState
import com.android.packageinstaller.v2.ui.SpecialRuntimePermUtils
import org.json.JSONObject
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.concurrent.FutureTask
import java.util.concurrent.TimeUnit

/** Owner-enrolled, one-request approval of ordinary Play Store package sessions. */
class AgentInstallProvider : ContentProvider() {
    companion object {
        private const val CLIENT = "dev.pixel.voice"
        private const val CLIENT_CERT = "__PIXEL_VOICE_SHA256__"
        private const val STORE = "com.android.vending"
        private const val STORE_CERT = "7ce83c1b71f3d572fed04c8d40c5cb10ff75e6d87d9df6fbd53f0468c2905053"
        private const val LEASE_MS = 10 * 60 * 1000L
    }
    private val main = Handler(Looper.getMainLooper())
    private val ctx get() = requireNotNull(context)
    private val pm get() = ctx.packageManager
    private val prefs get() = ctx.getSharedPreferences("agent-install-approval", Context.MODE_PRIVATE)

    override fun onCreate() = true
    private fun digest(bytes: ByteArray) = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { "%02x".format(it.toInt() and 255) }
    private fun certificates(info: PackageInfo): List<String> = info.signingInfo?.apkContentsSigners
        ?.map { digest(it.toByteArray()) }?.sorted() ?: emptyList()
    private fun packageInfo(name: String) = pm.getPackageInfo(name, PackageManager.GET_SIGNING_CERTIFICATES)
    private fun checkCaller() {
        val uid = Binder.getCallingUid()
        require(pm.getPackagesForUid(uid)?.toList() == listOf(CLIENT)) { "Caller not authorized" }
        require(packageInfo(CLIENT).applicationInfo?.uid == uid &&
            certificates(packageInfo(CLIENT)) == listOf(CLIENT_CERT)) { "Caller signature mismatch" }
        require(ctx.getSystemService(DevicePolicyManager::class.java).isDeviceOwnerApp(CLIENT)) {
            "Rose Agent is not the device owner"
        }
    }
    private fun available() {
        check(prefs.getBoolean("enabled", false)) { "Install approval is disabled" }
        check(ctx.getSystemService(UserManager::class.java).isUserUnlocked &&
            !ctx.getSystemService(KeyguardManager::class.java).isDeviceLocked) { "Unlock the phone first" }
        check(PackageUtil.isVersionTwoEnabled(ctx)) { "Installer version unsupported" }
    }
    private fun storeUid(): Int {
        val info = packageInfo(STORE)
        check(certificates(info) == listOf(STORE_CERT)) { "Play Store signature mismatch" }
        val uid = requireNotNull(info.applicationInfo).uid
        check(pm.getPackagesForUid(uid)?.toList() == listOf(STORE)) { "Shared Play Store identity unsupported" }
        return uid
    }
    private fun result(state: String, reason: String? = null) = JSONObject().put("status", state)
        .apply { if (reason != null) put("reason", reason) }
    private fun write(record: JSONObject) {
        check(prefs.edit().putString("request", record.toString()).commit()) { "Could not save approval state" }
    }
    private fun record() = prefs.getString("request", null)?.let { JSONObject(it) }
    private fun sameBoot(r: JSONObject) = r.getInt("boot") == Settings.Global.getInt(ctx.contentResolver, Settings.Global.BOOT_COUNT, -1)
    private fun packageAllowed(name: String) {
        require(name.matches(Regex("[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z][A-Za-z0-9_]*)+"))) { "Invalid package name" }
        require(name != CLIENT && name != STORE && name != ctx.packageName) { "Protected package" }
        try {
            val ai = requireNotNull(packageInfo(name).applicationInfo)
            require(ai.flags and (ApplicationInfo.FLAG_SYSTEM or ApplicationInfo.FLAG_UPDATED_SYSTEM_APP) == 0) {
                "System package updates are not supported"
            }
        } catch (_: PackageManager.NameNotFoundException) { }
    }
    private fun matchesLabel(session: PackageInstaller.SessionInfo, label: String): Boolean {
        val path = session.resolvedBaseApkPath ?: return false
        val info = pm.getPackageArchiveInfo(path, 0) ?: return false
        val ai = info.applicationInfo ?: return false
        ai.sourceDir = path; ai.publicSourceDir = path
        return pm.getApplicationLabel(ai).toString().trim().equals(label, ignoreCase = true)
    }
    private fun arm(p: JSONObject): JSONObject {
        available()
        val store = storeUid()
        val name = p.optString("packageName")
        val request = p.getString("requestId")
        require(request.length in 1..128) { "Invalid request" }
        val label = p.getString("label").trim()
        require(label.isNotEmpty() && label.length <= 200) { "Invalid app label" }
        if (name.isNotEmpty()) packageAllowed(name)
        val old = record()
        if (old != null && old.optString("requestId") == request && old.optString("requestedPackage") == name && old.optString("label") == label && sameBoot(old))
            return status(old).put("token", old.getString("token"))
        if (old != null && sameBoot(old) && old.optString("state") in listOf("armed", "approved_installing") &&
            SystemClock.elapsedRealtime() < old.optLong("expires"))
            error("Another install request is active")
        val token = ByteArray(32).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it.toInt() and 255) }
        val existing = pm.packageInstaller.allSessions
        // A fresh explicit request can also resume this same app's existing approval screen.
        val resumable = existing.filter { it.installerUid == store && it.installerPackageName == STORE &&
            it.isCommitted && it.isSealed && (name.isEmpty() && matchesLabel(it, label) || name.isNotEmpty() && it.appPackageName == name) }
        check(resumable.size <= 1) { "Multiple matching pending installs; approve manually" }
        val resumeIds = resumable.map { it.sessionId }.toSet()
        val r = JSONObject().put("packageName", name).put("requestedPackage", name).put("label", label).put("requestId", request).put("token", token)
            .put("boot", Settings.Global.getInt(ctx.contentResolver, Settings.Global.BOOT_COUNT, -1))
            .put("created", System.currentTimeMillis()).put("expires", SystemClock.elapsedRealtime() + LEASE_MS)
            .put("existingSessions", org.json.JSONArray(existing.map { it.sessionId }.filter { it !in resumeIds }))
            .put("resumeSessions", org.json.JSONArray(resumeIds.toList()))
            .put("state", "armed")
        write(r)
        return result("prepared").put("packageName", name).put("requestId", request).put("token", token)
    }
    private fun authorizedRecord(p: JSONObject): JSONObject {
        val r = record() ?: error("No prepared install")
        require(MessageDigest.isEqual(r.getString("token").toByteArray(), p.getString("token").toByteArray())) { "Request token mismatch" }
        require(r.getString("requestId") == p.getString("requestId")) { "Requested operation mismatch" }
        return r
    }
    private fun status(r: JSONObject): JSONObject {
        val name = r.getString("packageName")
        if (r.optString("state") == "approved_installing") {
            try {
                val info = packageInfo(name)
                if (info.longVersionCode == r.getLong("versionCode") &&
                    certificates(info).joinToString(",") == r.getString("signers") &&
                    info.lastUpdateTime >= r.getLong("created")) {
                    r.put("state", "installed"); write(r)
                }
            } catch (_: PackageManager.NameNotFoundException) { }
            if (r.optString("state") == "approved_installing" &&
                SystemClock.elapsedRealtime() - r.optLong("approvedAt") > 30_000 &&
                pm.packageInstaller.getSessionInfo(r.getInt("sessionId")) == null) {
                r.put("state", "failed").put("reason", "Install session ended without the expected installed package"); write(r)
            }
        }
        if (r.optString("state") == "armed" && (!sameBoot(r) || SystemClock.elapsedRealtime() >= r.getLong("expires"))) {
            r.put("state", "expired"); write(r)
        }
        if (r.optString("state") == "installed") AgentInstallUi.finishConfirmedSession(r.getInt("sessionId"))
        return result(if (r.getString("state") == "armed") "pending_download" else r.getString("state"), r.optString("reason").ifEmpty { null })
            .put("packageName", name).put("requestId", r.getString("requestId"))
            .apply { if (r.has("versionCode")) put("versionCode", r.getLong("versionCode")) }
    }
    private fun approve(r: JSONObject): JSONObject {
        available()
        val current = status(r)
        if (r.getString("state") != "armed") return current
        val uid = storeUid()
        var name = r.getString("packageName")
        val old = r.getJSONArray("existingSessions")
        val prior = (0 until old.length()).map { old.getInt(it) }.toSet()
        val resume = r.optJSONArray("resumeSessions") ?: org.json.JSONArray()
        val resumeIds = (0 until resume.length()).map { resume.getInt(it) }.toSet()
        val sessions = pm.packageInstaller.allSessions.filter {
            it.installerUid == uid && it.installerPackageName == STORE && (name.isEmpty() || it.appPackageName == name) &&
                it.sessionId !in prior && (it.createdMillis >= r.getLong("created") || it.sessionId in resumeIds) &&
                it.isCommitted && it.isSealed
        }.filter {
            name.isNotEmpty() || matchesLabel(it, r.getString("label"))
        }
        if (sessions.isEmpty()) return result("pending_download").put("packageName", name)
        check(sessions.size == 1) { "Multiple matching sessions; approve manually" }
        val s = sessions.single()
        name = s.appPackageName ?: error("Missing package identity")
        packageAllowed(name)
        check(!s.isMultiPackage && s.parentSessionId == PackageInstaller.SessionInfo.INVALID_ID && !s.isStaged) {
            "Multi-package or staged installs require manual approval"
        }
        check(s.pendingUserActionReason == PackageInstaller.REASON_CONFIRM_PACKAGE_CHANGE) {
            "Additional installer policy requires manual approval"
        }
        val path = s.resolvedBaseApkPath ?: return result("pending_download")
        val apk = pm.getPackageArchiveInfo(path, PackageManager.GET_PERMISSIONS or PackageManager.GET_SIGNING_CERTIFICATES)
            ?: error("Cannot read staged application")
        check(apk.packageName == name && certificates(apk).isNotEmpty()) { "Staged application identity mismatch" }
        try {
            val installed = packageInfo(name)
            check(apk.longVersionCode > installed.longVersionCode && certificates(apk) == certificates(installed)) {
                "Update version or signing identity requires manual approval"
            }
        } catch (_: PackageManager.NameNotFoundException) { }
        // Reuse Android's ordinary source, user restriction, package and ownership checks.
        val repo = InstallRepository(ctx)
        val intent = Intent(PackageInstaller.ACTION_CONFIRM_INSTALL).putExtra(PackageInstaller.EXTRA_SESSION_ID, s.sessionId)
        check(repo.performPreInstallChecks(intent, InstallRepository.CallerInfo(STORE, uid)).stageCode == InstallStage.STAGE_STAGING) {
            "Normal installer pre-checks did not pass"
        }
        repo.stageForInstall()
        check(repo.stagingResult.value?.stageCode == InstallStage.STAGE_READY) { "Session not ready" }
        val confirmation = repo.requestUserConfirmation(true)
        check(confirmation is InstallUserActionRequired &&
            confirmation.actionReason == InstallUserActionRequired.USER_ACTION_REASON_INSTALL_CONFIRMATION &&
            (confirmation.existingUpdateOwnerPackageName.isNullOrEmpty() || confirmation.existingUpdateOwnerPackageName == STORE) &&
            confirmation.requestedUpdateOwnerPackageName.isNullOrEmpty()) { "Additional installer confirmation requires manual approval (" + (confirmation?.javaClass?.simpleName ?: "none") + ")" }
        val permissions = ArrayList<SpecialPermissionState>()
        if (!confirmation.isAppUpdating) {
            // Match the existing visible install dialog's Network checkbox default.
            val network = CheckBox(ctx).apply { id = R.id.install_allow_INTERNET_permission; isChecked = true; visibility = View.GONE }
            SpecialRuntimePermUtils.setInitialCheckboxStates(apk, network)
            SpecialRuntimePermUtils.collectCheckboxState(network, permissions)
        }
        // Commit before acceptance: a reconnect can inspect, but cannot accept a second session.
        r.put("packageName", name).put("state", "approved_installing").put("sessionId", s.sessionId)
            .put("approvedAt", SystemClock.elapsedRealtime())
            .put("versionCode", apk.longVersionCode).put("signers", certificates(apk).joinToString(","))
        write(r)
        repo.initiateInstall(permissions)
        return status(r)
    }
    override fun call(method: String, arg: String?, extras: Bundle?): Bundle {
        checkCaller()
        val params = JSONObject(extras?.getString("params") ?: "{}")
        val task = FutureTask {
            try {
                when (method) {
                    "configure" -> {
                        check(prefs.edit().putBoolean("enabled", params.getBoolean("enabled")).remove("request").commit())
                        result("configured").put("enabled", prefs.getBoolean("enabled", false))
                    }
                    "arm" -> arm(params)
                    "approve" -> approve(authorizedRecord(params))
                    "status" -> status(authorizedRecord(params))
                    "revoke" -> authorizedRecord(params).let { it.put("state", "revoked"); write(it); status(it) }
                    else -> error("Unsupported operation")
                }
            } catch (e: Exception) { result("error", e.message ?: "Install approval failed") }
        }
        main.post(task)
        return Bundle().apply { putString("result", task.get(12, TimeUnit.SECONDS).toString()) }
    }
    override fun query(uri: Uri, projection: Array<out String>?, selection: String?, selectionArgs: Array<out String>?, sortOrder: String?): Cursor? = null
    override fun getType(uri: Uri): String? = null
    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?) = 0
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?) = 0
}
