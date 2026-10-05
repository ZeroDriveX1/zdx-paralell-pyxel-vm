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
    private lateinit var workEvents: WorkEventStore
    private val handler = Handler(Looper.getMainLooper())
    private val ioExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private val cycleActive = AtomicBoolean(false)
    private val serviceActive = AtomicBoolean(false)
    @Volatile private var retryDelayMs = BASE_RETRY_MS
    @Volatile private var transport: ZdxMeshTransport? = null

    private val monitor = object : Runnable {
        override fun run() {
            if (!serviceActive.get()) return
            val settings = meshStore.load()
            var cycleStarted = false
            if (settings.enabled && settings.transportConfig().isConfigured() && cycleActive.compareAndSet(false, true)) {
                cycleStarted = true
                ioExecutor.execute { runMeshCycle(settings) }
            } else if (!settings.enabled) {
                updateStatus(false, "disabled", workState = "DISABLED")
            } else if (!settings.transportConfig().isConfigured()) {
                updateStatus(false, "mesh not configured", workState = "UNCONFIGURED")
            }
            if (!cycleStarted && serviceActive.get()) {
                handler.postDelayed(this, retryDelayMs)
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        policyStore = ResourcePolicyStore(this)
        meshStore = MeshSettingsStore(this)
        admission = AndroidResourceAdmission(this)
        taskExecutor = AndroidTaskExecutor(this)
        workEvents = WorkEventStore(this)
        workEvents.append("SERVICE", "background node service created")
        createNotificationChannel()
        startForeground(1001, notification("starting"))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val control = getSharedPreferences(CONTROL_PREFS, MODE_PRIVATE)
        if (intent?.action == ACTION_STOP) {
            control.edit().putBoolean(KEY_SERVICE_REQUESTED, false).commit()
            serviceActive.set(false)
            handler.removeCallbacks(monitor)
            transport?.close()
            if (::workEvents.isInitialized) workEvents.append("SERVICE", "background node service stopped by user")
            stopSelfResult(startId)
            return START_NOT_STICKY
        }
        val requested = intent?.action == ACTION_START ||
            control.getBoolean(KEY_SERVICE_REQUESTED, false)
        if (!requested) {
            stopSelfResult(startId)
            return START_NOT_STICKY
        }
        control.edit().putBoolean(KEY_SERVICE_REQUESTED, true).apply()
        serviceActive.set(true)
        retryDelayMs = BASE_RETRY_MS
        if (!cycleActive.get()) {
            handler.removeCallbacks(monitor)
            handler.post(monitor)
        }
        return START_STICKY
    }
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        serviceActive.set(false)
        handler.removeCallbacks(monitor)
        transport?.close()
        ioExecutor.shutdownNow()
        super.onDestroy()
    }

    private fun runMeshCycle(settings: MeshSettings) {
        if (!serviceActive.get() || !settings.enabled) {
            cycleActive.set(false)
            return
        }
        var taskId = ""
        var leaseId: String? = null
        val nodeTransport = ZdxMeshTransport(this, settings.transportConfig())
        transport = nodeTransport
        try {
            val policy = policyStore.load()
            recordWork("CONNECTING", "registering node and capabilities", direction = "outgoing")
            val capability = ZdxProtocol(this).capabilityReportJson()
                .put("android_vm_adapter_protocol", ANDROID_VM_ADAPTER_PROTOCOL)
                .put("android_vm_adapters", taskExecutor.advertisedAdapters())
            nodeTransport.register(capability)
            retryDelayMs = BASE_RETRY_MS
            recordWork("CONNECTED", "node registered with mesh", direction = "outgoing")
            val snapshot = admission.snapshot()
            val policyBlock = admission.blockReason(policy, snapshot)
            if (policyBlock != null) {
                updateStatus(true, "connected; compute paused: $policyBlock", workState = "POLICY_BLOCKED")
                recordWork("POLICY", "compute paused: $policyBlock")
                return
            }
            recordWork("POLL", "requesting available work", direction = "outgoing")
            val poll = nodeTransport.poll(
                (snapshot.availableMemoryMb - policy.minFreeMemoryMb).coerceAtLeast(0),
                Runtime.getRuntime().availableProcessors(), 0.0
            )
            if (poll.optString("kind") != "compute_task") throw IOException("unexpected poll response")
            val payload = poll.optJSONObject("payload") ?: throw IOException("poll response has no payload")
            val rawTask = payload.opt("task")
            if (rawTask == null || rawTask == JSONObject.NULL) {
                updateStatus(true, "connected; no task available", workState = "IDLE")
                return
            }
            val task = rawTask as? JSONObject ?: throw IOException("poll task is malformed")
            taskId = task.optString("task_id")
            recordWork("INCOMING", "task received from coordinator", taskId, "incoming")
            leaseId = task.optJSONObject("metadata")?.optString("lease_id")?.takeIf { it.isNotBlank() }
            val digest = task.optString("artifact_digest")
            val memoryMb = task.optInt("memory_mb", Int.MAX_VALUE)
            if (taskId.isBlank() || digest.isBlank()) throw IOException("Android requires an artifact-backed task")
            val taskPolicyBlock = admission.blockReason(policy)
            if (taskPolicyBlock != null || memoryMb > policy.memoryLimitMb) {
                val reason = taskPolicyBlock ?: "task requests $memoryMb MB above configured ${policy.memoryLimitMb} MB limit"
                nodeTransport.release(taskId, "Android resource policy rejected task: $reason", leaseId)
                recordWork("RELEASED", reason, taskId, "outgoing")
                updateStatus(true, "task released: $reason", workState = "RELEASED", taskId = taskId, terminal = true)
                return
            }
            updateStatus(true, "queued $taskId", workState = "QUEUED", taskId = taskId)
            recordWork("QUEUED", "task admitted by local resource policy", taskId, "incoming")
            var lastPercent = -1
            updateStatus(true, "receiving $taskId", workState = "RECEIVING", taskId = taskId)
            val artifact = nodeTransport.receiveArtifact(
                taskId,
                digest,
                shouldContinue = { admission.canRun(policyStore.load()) },
                onProgress = { received, total ->
                    val percent = if (total > 0) ((received * 100L) / total).toInt() else 0
                    if (percent != lastPercent) {
                        lastPercent = percent
                        updateStatus(true, "receiving $taskId · $percent%", workState = "RECEIVING", taskId = taskId, progress = percent)
                        workEvents.append("DOWNLOAD", "$received/$total bytes ($percent%)", taskId, "incoming")
                    }
                }
            )
            val executionPolicy = policyStore.load()
            val executionBlock = admission.blockReason(executionPolicy)
            if (executionBlock != null || memoryMb > executionPolicy.memoryLimitMb) {
                val reason = executionBlock
                    ?: "task requests $memoryMb MB above current ${executionPolicy.memoryLimitMb} MB limit"
                artifact.delete()
                nodeTransport.release(taskId, "Android resource policy changed before execution: $reason", leaseId)
                recordWork("RELEASED", "policy changed before execution: $reason", taskId, "outgoing")
                updateStatus(true, "task released: $reason", workState = "RELEASED", taskId = taskId, terminal = true)
                return
            }
            updateStatus(true, "running $taskId", workState = "RUNNING", taskId = taskId, progress = 100)
            recordWork("RUNNING", "artifact verified; adapter execution started", taskId)
            val startedAt = System.currentTimeMillis()
            val result = taskExecutor.execute(task, artifact, executionPolicy)
            val elapsed = System.currentTimeMillis() - startedAt
            recordWork("OUTGOING", "sending result after ${elapsed}ms compute", taskId, "outgoing")
            nodeTransport.complete(taskId, AndroidResultAttestor(this).attest(result), leaseId)
            saveLastTaskDetail(taskId, result, elapsed)
            recordWork("COMPLETED", "result acknowledged by coordinator · ${elapsed}ms", taskId, "outgoing")
            updateStatus(true, "completed $taskId · ${elapsed}ms", workState = "COMPLETED", taskId = taskId, terminal = true)
            artifact.delete()
        } catch (_: ZdxMeshPreemptedException) {
            if (taskId.isNotBlank()) safeRelease(nodeTransport, taskId, "Android became busy or stopped charging", leaseId)
            recordWork("RELEASED", "device became active or charging/policy changed", taskId.takeIf { it.isNotBlank() }, "outgoing")
            updateStatus(false, "task yielded to device workload", workState = "RELEASED", taskId = taskId, terminal = true)
        } catch (cancelled: AndroidVmExecutionCancelledException) {
            if (taskId.isNotBlank()) safeRelease(nodeTransport, taskId, cancelled.message ?: "Android execution cancelled", leaseId)
            recordWork("RELEASED", cancelled.message ?: "execution cancelled by resource policy", taskId.takeIf { it.isNotBlank() }, "outgoing")
            updateStatus(false, "task yielded to device workload", workState = "RELEASED", taskId = taskId, terminal = true)
        } catch (timeout: AndroidVmExecutionTimeoutException) {
            if (taskId.isNotBlank()) safeFail(nodeTransport, taskId, timeout.message ?: "Android execution timed out", leaseId)
            recordWork("FAILED", timeout.message ?: "execution timeout", taskId.takeIf { it.isNotBlank() }, "outgoing")
            updateStatus(true, "task failed: execution timeout", workState = "FAILED", taskId = taskId, terminal = true)
        } catch (unsupported: UnsupportedOperationException) {
            if (taskId.isNotBlank()) safeFail(nodeTransport, taskId, unsupported.message ?: "unsupported Android execution", leaseId)
            recordWork("FAILED", unsupported.message ?: "Android VM adapter unavailable", taskId.takeIf { it.isNotBlank() }, "outgoing")
            updateStatus(true, "task rejected: Android VM adapter unavailable", workState = "FAILED", taskId = taskId, terminal = true)
        } catch (error: Exception) {
            retryDelayMs = (retryDelayMs * 2).coerceAtMost(MAX_RETRY_MS)
            // Keep the lease recoverable on transport/device failure; the coordinator will expire it.
            val detail = error.message?.replace(Regex("[\\r\\n]"), " ")?.trim()?.take(120).orEmpty()
            recordWork("ERROR", "${error.javaClass.simpleName}" + if (detail.isBlank()) "" else ": $detail", taskId.takeIf { it.isNotBlank() })
            updateStatus(false, "mesh unavailable: ${error.javaClass.simpleName}" + if (detail.isBlank()) "" else " ($detail)", workState = "ERROR", taskId = taskId)
        } finally {
            nodeTransport.close()
            transport = null
            cycleActive.set(false)
            if (serviceActive.get()) {
                handler.postDelayed(monitor, retryDelayMs)
            }
        }
    }

    private fun safeRelease(nodeTransport: ZdxMeshTransport, taskId: String, reason: String, leaseId: String?) {
        try { nodeTransport.release(taskId, reason.take(4_000), leaseId) } catch (_: Exception) { }
    }

    private fun safeFail(nodeTransport: ZdxMeshTransport, taskId: String, reason: String, leaseId: String?) {
        try { nodeTransport.fail(taskId, reason.take(4_000), leaseId) } catch (_: Exception) { }
    }

    private fun saveLastTaskDetail(taskId: String, result: JSONObject, elapsedMs: Long) {
        val adapter = result.optString("adapter_id").takeIf { it.isNotBlank() } ?: "unknown adapter"
        val status = result.optString("status").takeIf { it.isNotBlank() } ?: "completed"
        val completedWork = result.optInt("completed_work", 0)
        val detail = "$status · $adapter · work=$completedWork · ${elapsedMs}ms"
        getSharedPreferences("zdx_status", MODE_PRIVATE).edit()
            .putString("last_task_id", taskId)
            .putString("last_task_detail", detail.take(300))
            .putLong("last_task_at", System.currentTimeMillis())
            .apply()
    }

    private fun recordWork(type: String, message: String, taskId: String? = null, direction: String? = null) {
        workEvents.append(type, message, taskId, direction)
    }

    private fun updateStatus(
        connected: Boolean,
        message: String,
        workState: String? = null,
        taskId: String = "",
        progress: Int? = null,
        terminal: Boolean = false
    ) {
        val preferences = getSharedPreferences("zdx_status", MODE_PRIVATE)
        val editor = preferences.edit()
            .putBoolean("connected", connected)
            .putLong("last_update", System.currentTimeMillis())
            .putString("message", message.take(200))
        if (workState != null) editor.putString("work_state", workState)
        if (taskId.isNotBlank()) {
            editor.putString("current_task_id", taskId)
            if (terminal) {
                editor.putString("last_task_id", taskId)
                    .putString("last_task_state", workState ?: "")
                    .putLong("last_task_at", System.currentTimeMillis())
            }
        }
        if (progress != null) editor.putInt("progress", progress.coerceIn(0, 100))
        if (terminal) {
            editor.remove("current_task_id")
            editor.remove("progress")
        }
        editor.apply()
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

    companion object {
        const val ACTION_START = "com.zerodrivex.zdxnode.action.START"
        const val ACTION_STOP = "com.zerodrivex.zdxnode.action.STOP"
        const val CONTROL_PREFS = "zdx_service_control"
        const val KEY_SERVICE_REQUESTED = "service_requested"
        const val BASE_RETRY_MS = 15_000L
        const val MAX_RETRY_MS = 15 * 60_000L
    }
}
