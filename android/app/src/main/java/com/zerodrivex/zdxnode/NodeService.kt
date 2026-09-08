package com.zerodrivex.zdxnode

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** Foreground lifecycle plus authenticated mesh registration and bounded polling. */
class NodeService : Service() {
    private lateinit var policyStore: ResourcePolicyStore
    private lateinit var meshStore: MeshSettingsStore
    private lateinit var admission: AndroidResourceAdmission
    private lateinit var taskExecutor: AndroidTaskExecutor
    private val handler = Handler(Looper.getMainLooper())
    private val ioExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private val cycleActive = AtomicBoolean(false)
    @Volatile private var transport: ZdxMeshTransport? = null

    private val monitor = object : Runnable {
        override fun run() {
            val policy = policyStore.load()
            val settings = meshStore.load()
            if (settings.transportConfig().isConfigured() && admission.canRun(policy) && cycleActive.compareAndSet(false, true)) {
                ioExecutor.execute { runMeshCycle(settings) }
            } else if (!settings.enabled) {
                updateStatus(false, "disabled")
            } else if (!settings.transportConfig().isConfigured()) {
                updateStatus(false, "mesh not configured")
            } else if (!admission.canRun(policy)) {
                updateStatus(false, "paused by device resource policy")
            }
            handler.postDelayed(this, 15_000L)
        }
    }

    override fun onCreate() {
        super.onCreate()
        policyStore = ResourcePolicyStore(this)
        meshStore = MeshSettingsStore(this)
        admission = AndroidResourceAdmission(this)
        taskExecutor = AndroidTaskExecutor(this)
        createNotificationChannel()
        startForeground(1001, notification("starting"))
        handler.post(monitor)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        handler.removeCallbacks(monitor)
        transport?.close()
        ioExecutor.shutdownNow()
        super.onDestroy()
    }

    private fun runMeshCycle(settings: MeshSettings) {
        var taskId = ""
        var leaseId: String? = null
        val nodeTransport = ZdxMeshTransport(this, settings.transportConfig())
        transport = nodeTransport
        try {
            val policy = policyStore.load()
            if (!admission.canRun(policy)) return
            val capability = ZdxProtocol(this).capabilityReportJson()
                .put("android_vm_adapter_protocol", ANDROID_VM_ADAPTER_PROTOCOL)
                .put("android_vm_adapters", taskExecutor.advertisedAdapters())
            nodeTransport.register(capability)
            val snapshot = admission.snapshot()
            val poll = nodeTransport.poll(
                (snapshot.availableMemoryMb - policy.minFreeMemoryMb).coerceAtLeast(0),
                Runtime.getRuntime().availableProcessors(), 0.0
            )
            if (poll.optString("kind") != "compute_task") throw IOException("unexpected poll response")
            val payload = poll.optJSONObject("payload") ?: throw IOException("poll response has no payload")
            val rawTask = payload.opt("task")
            if (rawTask == null || rawTask == JSONObject.NULL) {
                updateStatus(true, "connected; no task available")
                return
            }
            val task = rawTask as? JSONObject ?: throw IOException("poll task is malformed")
            taskId = task.optString("task_id")
            leaseId = task.optJSONObject("metadata")?.optString("lease_id")?.takeIf { it.isNotBlank() }
            val digest = task.optString("artifact_digest")
            val memoryMb = task.optInt("memory_mb", Int.MAX_VALUE)
            if (taskId.isBlank() || digest.isBlank()) throw IOException("Android requires an artifact-backed task")
            if (!admission.canRun(policy) || memoryMb > policy.memoryLimitMb) {
                nodeTransport.release(taskId, "Android resource policy rejected task", leaseId)
                updateStatus(true, "task released by resource policy")
                return
            }
            updateStatus(true, "receiving $taskId")
            val artifact = nodeTransport.receiveArtifact(taskId, digest) { admission.canRun(policyStore.load()) }
            val result = taskExecutor.execute(task, artifact, policyStore.load())
            nodeTransport.complete(taskId, AndroidResultAttestor(this).attest(result), leaseId)
            updateStatus(true, "completed $taskId")
            artifact.delete()
        } catch (_: ZdxMeshPreemptedException) {
            if (taskId.isNotBlank()) safeRelease(nodeTransport, taskId, "Android became busy or stopped charging", leaseId)
            updateStatus(false, "task yielded to device workload")
        } catch (cancelled: AndroidVmExecutionCancelledException) {
            if (taskId.isNotBlank()) safeRelease(nodeTransport, taskId, cancelled.message ?: "Android execution cancelled", leaseId)
            updateStatus(false, "task yielded to device workload")
        } catch (timeout: AndroidVmExecutionTimeoutException) {
            if (taskId.isNotBlank()) safeFail(nodeTransport, taskId, timeout.message ?: "Android execution timed out", leaseId)
            updateStatus(true, "task failed: execution timeout")
        } catch (unsupported: UnsupportedOperationException) {
            if (taskId.isNotBlank()) safeFail(nodeTransport, taskId, unsupported.message ?: "unsupported Android execution", leaseId)
            updateStatus(true, "task rejected: Android VM adapter unavailable")
        } catch (error: Exception) {
            // Keep the lease recoverable on transport/device failure; the coordinator will expire it.
            val detail = error.message?.replace(Regex("[\\r\\n]"), " ")?.trim()?.take(120).orEmpty()
            updateStatus(false, "mesh unavailable: ${error.javaClass.simpleName}" + if (detail.isBlank()) "" else " ($detail)")
        } finally {
            nodeTransport.close()
            transport = null
            cycleActive.set(false)
        }
    }

    private fun safeRelease(nodeTransport: ZdxMeshTransport, taskId: String, reason: String, leaseId: String?) {
        try { nodeTransport.release(taskId, reason.take(4_000), leaseId) } catch (_: Exception) { }
    }

    private fun safeFail(nodeTransport: ZdxMeshTransport, taskId: String, reason: String, leaseId: String?) {
        try { nodeTransport.fail(taskId, reason.take(4_000), leaseId) } catch (_: Exception) { }
    }

    private fun updateStatus(connected: Boolean, message: String) {
        getSharedPreferences("zdx_status", MODE_PRIVATE).edit()
            .putBoolean("connected", connected).putLong("last_update", System.currentTimeMillis())
            .putString("message", message.take(200)).apply()
        handler.post { getSystemService(NotificationManager::class.java)?.notify(1001, notification(message)) }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            getSystemService(NotificationManager::class.java).createNotificationChannel(
                NotificationChannel("zdx_node", "ZDX compute node", NotificationManager.IMPORTANCE_LOW)
            )
        }
    }

    private fun notification(message: String): Notification = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        Notification.Builder(this, "zdx_node").setContentTitle("ZDX Node").setContentText(message)
            .setStyle(Notification.BigTextStyle().bigText(message)).setContentIntent(openSettingsIntent())
            .setSmallIcon(android.R.drawable.stat_notify_sync).setOngoing(true).build()
    } else {
        Notification.Builder(this).setContentTitle("ZDX Node").setContentText(message)
            .setStyle(Notification.BigTextStyle().bigText(message)).setContentIntent(openSettingsIntent())
            .setSmallIcon(android.R.drawable.stat_notify_sync).setOngoing(true).build()
    }

    private fun openSettingsIntent(): PendingIntent = PendingIntent.getActivity(
        this, 0,
        Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
    )
}
