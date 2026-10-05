package com.zerodrivex.zdxnode

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import android.graphics.drawable.GradientDrawable

/** Clear, dependency-free control surface for identity, mesh, policy, and background execution. */
class MainActivity : Activity() {
    private val mainHandler = Handler(Looper.getMainLooper())
    private lateinit var statusView: TextView
    private lateinit var capabilityView: TextView
    private lateinit var workView: TextView
    private lateinit var consoleView: TextView

    private val refreshUi = object : Runnable {
        override fun run() {
            refreshStatus()
            mainHandler.postDelayed(this, 2_000L)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 42)

        val policyStore = ResourcePolicyStore(this)
        val meshStore = MeshSettingsStore(this)
        val capability = DeviceCapabilityCollector(this).collect()
        val mesh = meshStore.load()
        val policy = policyStore.load()
        val nodeId = ZdxProtocol(this).nodeId()

        val enabled = Switch(this).apply { isChecked = policy.enabled }
        val idle = Switch(this).apply { isChecked = policy.idleOnly }
        val charging = Switch(this).apply { isChecked = policy.requireCharging }
        val meshEnabled = Switch(this).apply { isChecked = mesh.enabled }
        val memoryLimit = edit("Compute memory limit (MB)", policy.memoryLimitMb.toString(), false).apply {
            inputType = InputType.TYPE_CLASS_NUMBER
        }
        val minFreeMemory = edit("Minimum free RAM to preserve (MB)", policy.minFreeMemoryMb.toString(), false).apply {
            inputType = InputType.TYPE_CLASS_NUMBER
        }
        val host = edit("Server or peer hostname", mesh.host, false)
        val port = edit("TLS port", mesh.port.toString(), false).apply { inputType = InputType.TYPE_CLASS_NUMBER }
        val ca = edit("Private CA certificate PEM", mesh.caCertificatePem, true)

        statusView = TextView(this).apply { setTextSize(16f); setPadding(dp(8), dp(8), dp(8), dp(8)) }
        capabilityView = TextView(this).apply { setTextSize(15f); setTextColor(Color.rgb(55, 65, 81)) }
        workView = TextView(this).apply { setTextSize(14f); setTextColor(Color.rgb(55, 65, 81)) }
        consoleView = TextView(this).apply {
            setTextSize(12f)
            setTypeface(Typeface.MONOSPACE, Typeface.NORMAL)
            setTextColor(Color.rgb(31, 41, 55))
            setTextIsSelectable(true)
            setPadding(dp(10), dp(10), dp(10), dp(10))
            background = rounded(Color.rgb(248, 250, 252), 10)
        }

        val root = ScrollView(this).apply { setBackgroundColor(Color.rgb(245, 247, 250)) }
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(20), dp(20), dp(28))
        }

        content.addView(TextView(this).apply {
            text = "ZDX Node"
            setTextSize(30f)
            setTypeface(Typeface.DEFAULT, Typeface.BOLD)
            setTextColor(Color.rgb(17, 24, 39))
        })
        content.addView(TextView(this).apply {
            text = "Private mesh compute node · runs only under your policy"
            setTextSize(15f)
            setTextColor(Color.rgb(75, 85, 99))
            setPadding(0, dp(4), 0, dp(16))
        })

        val identity = section("Node identity")
        identity.addView(label("Stable device identifier"))
        identity.addView(TextView(this).apply {
            text = nodeId
            setTextIsSelectable(true)
            setTextSize(14f)
            setTypeface(Typeface.MONOSPACE, Typeface.NORMAL)
            setTextColor(Color.rgb(31, 41, 55))
            setPadding(0, dp(6), 0, 0)
        })
        content.addView(identity, margins())

        val capabilities = section("Device capability")
        capabilities.addView(capabilityView)
        content.addView(capabilities, margins())

        val status = section("Live service status")
        status.addView(statusView)
        status.addView(workView)
        content.addView(status, margins())

        val console = section("Work console")
        console.addView(label("Incoming, queued, receiving, running, completed, released, failed, and outgoing work. Newest events appear at the bottom."))
        console.addView(consoleView, fieldMargins())
        console.addView(button("Clear console", false) {
            WorkEventStore(this).clear()
            refreshStatus()
        })
        content.addView(console, margins())

        val participation = section("Background participation")
        participation.addView(label("These controls protect the phone. Changes apply after saving."))
        addSwitch(participation, enabled, "Enable background compute", "Allow this node to receive authorized work.")
        addSwitch(participation, idle, "Only run while device is idle", "Recommended: wait until the screen is off and the device is idle.")
        addSwitch(participation, charging, "Require charging", "Never run compute while the phone is on battery.")
        participation.addView(label("Compute memory limit"))
        participation.addView(memoryLimit, fieldMargins())
        participation.addView(label("Free-memory reserve kept available for Android and foreground apps"))
        participation.addView(minFreeMemory, fieldMargins())
        addSwitch(participation, meshEnabled, "Join configured mesh", "Register capabilities and distribute authorized artifacts over TLS.")
        content.addView(participation, margins())

        val meshSection = section("Mesh connection")
        meshSection.addView(label("Use the server/peer hostname and private CA certificate. The Ed25519 node key stays in Android Keystore; no key path is needed here."))
        meshSection.addView(host, fieldMargins())
        meshSection.addView(port, fieldMargins())
        meshSection.addView(ca, fieldMargins())
        content.addView(meshSection, margins())

        fun saveSettings(): Boolean = try {
            val memoryLimitMb = memoryLimit.text.toString().toIntOrNull()
                ?: throw IllegalArgumentException("compute memory limit must be an integer")
            val minFreeMemoryMb = minFreeMemory.text.toString().toIntOrNull()
                ?: throw IllegalArgumentException("minimum free memory must be an integer")
            policyStore.save(policy.copy(
                enabled = enabled.isChecked,
                idleOnly = idle.isChecked,
                requireCharging = charging.isChecked,
                memoryLimitMb = memoryLimitMb,
                minFreeMemoryMb = minFreeMemoryMb
            ))
            meshStore.save(MeshSettings(host.text.toString(), port.text.toString().toIntOrNull() ?: 8765, meshEnabled.isChecked, ca.text.toString()))
            setLocalStatus("Settings saved. Start the background service when ready.")
            true
        } catch (error: IllegalArgumentException) {
            setLocalStatus("Could not save settings: ${error.message ?: "invalid value"}")
            false
        }

        val actions = section("Actions")
        actions.addView(button("Save settings", true) { saveSettings() })
        actions.addView(button("Start background service", false) {
            if (saveSettings()) {
                startForegroundService(Intent(this, NodeService::class.java).setAction(NodeService.ACTION_START))
                setLocalStatus("Starting background service. You can dismiss this screen; the notification will remain.")
            }
        })
        actions.addView(button("Dismiss and run in background", false) {
            if (saveSettings()) {
                startForegroundService(Intent(this, NodeService::class.java).setAction(NodeService.ACTION_START))
                finish()
            }
        })
        actions.addView(button("Stop background service", false) {
            getSharedPreferences(NodeService.CONTROL_PREFS, MODE_PRIVATE).edit()
                .putBoolean(NodeService.KEY_SERVICE_REQUESTED, false).commit()
            stopService(Intent(this, NodeService::class.java))
            setLocalStatus("Background service stopped.")
        })
        content.addView(actions, margins())

        content.addView(TextView(this).apply {
            text = "The notification is the background control surface. Tap it to reopen settings; stop the service from the app or Android settings."
            setTextSize(13f)
            setTextColor(Color.rgb(107, 114, 128))
            setPadding(dp(4), dp(16), dp(4), 0)
        })

        root.addView(content)
        setContentView(root)
        refreshStatus()
    }

    override fun onResume() {
        super.onResume()
        mainHandler.post(refreshUi)
    }

    override fun onPause() {
        mainHandler.removeCallbacks(refreshUi)
        super.onPause()
    }

    private fun refreshStatus() {
        if (!::statusView.isInitialized) return
        val status = getSharedPreferences("zdx_status", MODE_PRIVATE)
        val message = status.getString("message", "not started") ?: "not started"
        val state = status.getString("work_state", "STOPPED") ?: "STOPPED"
        val currentTask = status.getString("current_task_id", null)
        val lastTask = status.getString("last_task_id", null)
        val lastTaskState = status.getString("last_task_state", null)
        val progress = if (status.contains("progress")) status.getInt("progress", 0) else null

        statusView.text = message
        statusView.setTextColor(
            if (state in setOf("CONNECTED", "IDLE", "RUNNING", "COMPLETED")) Color.rgb(22, 101, 52)
            else Color.rgb(55, 65, 81)
        )

        val policy = ResourcePolicyStore(this).load()
        val admission = AndroidResourceAdmission(this)
        val snapshot = admission.snapshot()
        val policyBlock = admission.blockReason(policy, snapshot)
        workView.text = buildString {
            append("State: ").append(state)
            if (!currentTask.isNullOrBlank()) append("\nCurrent task: ").append(currentTask)
            if (progress != null) append(" · ").append(progress).append("%")
            if (!lastTask.isNullOrBlank()) {
                append("\nLast task: ").append(lastTask)
                if (!lastTaskState.isNullOrBlank()) append(" · ").append(lastTaskState)
            }
            append("\nPolicy: ").append(if (policyBlock == null) "eligible" else "paused · $policyBlock")
            append("\nIdle: ").append(if (snapshot.userIdle) "yes" else "no")
            append(" · charging: ").append(if (snapshot.charging) "yes" else "no")
            append(" · free RAM: ").append(snapshot.availableMemoryMb).append(" MB")
            append("\nLimit: ").append(policy.memoryLimitMb).append(" MB compute")
            append(" · preserve ").append(policy.minFreeMemoryMb).append(" MB free")
        }

        capabilityView.text = capabilitySummary(DeviceCapabilityCollector(this).collect())
        consoleView.text = WorkEventStore(this).consoleText(80)
    }

    private fun setLocalStatus(message: String) {
        getSharedPreferences("zdx_status", MODE_PRIVATE).edit()
            .putBoolean("connected", false).putLong("last_update", System.currentTimeMillis())
            .putString("message", message.take(200)).apply()
        statusView.text = message
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }

    private fun capabilitySummary(capability: NodeCapability): String =
        "${capability.cpu} · ${Runtime.getRuntime().availableProcessors()} cores\n" +
            "RAM ${capability.memoryMb} MB total · ${capability.availableMemoryMb} MB free\n" +
            "Vulkan ${if (capability.gpu.vulkanSupported) "available" else "not detected"} · " +
            "NNAPI ${if (capability.npu.available) "available" else "not detected"}\n" +
            "Charging ${if (capability.charging) "yes" else "no"}"

    private fun section(title: String): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(16), dp(14), dp(16), dp(16))
        background = rounded(Color.WHITE, 14)
        addView(TextView(this@MainActivity).apply {
            text = title
            setTextSize(18f)
            setTypeface(Typeface.DEFAULT, Typeface.BOLD)
            setTextColor(Color.rgb(17, 24, 39))
            setPadding(0, 0, 0, dp(10))
        })
    }

    private fun addSwitch(parent: LinearLayout, toggle: Switch, title: String, description: String) {
        toggle.text = "$title\n$description"
        toggle.setTextSize(15f)
        toggle.setTextColor(Color.rgb(31, 41, 55))
        toggle.setPadding(0, dp(8), 0, dp(8))
        parent.addView(toggle, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
    }

    private fun label(text: String): TextView = TextView(this).apply {
        this.text = text
        setTextSize(13f)
        setTextColor(Color.rgb(75, 85, 99))
    }

    private fun button(text: String, primary: Boolean, action: () -> Unit): Button = Button(this).apply {
        this.text = text
        isAllCaps = false
        setTextSize(15f)
        if (primary) setTextColor(Color.WHITE)
        setOnClickListener { action() }
        layoutParams = fieldMargins()
    }

    private fun edit(hintText: String, value: String, multiline: Boolean): EditText = EditText(this).apply {
        hint = hintText
        setText(value)
        setTextSize(15f)
        setPadding(dp(12), dp(10), dp(12), dp(10))
        inputType = if (multiline) InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE else InputType.TYPE_CLASS_TEXT
        if (multiline) {
            minLines = 4
            gravity = Gravity.TOP
        }
        layoutParams = fieldMargins()
    }

    private fun margins(): LinearLayout.LayoutParams = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
    ).apply { topMargin = dp(12) }

    private fun fieldMargins(): LinearLayout.LayoutParams = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
    ).apply { topMargin = dp(8) }

    private fun rounded(color: Int, radius: Int): GradientDrawable = GradientDrawable().apply {
        setColor(color)
        cornerRadius = dp(radius).toFloat()
        setStroke(dp(1), Color.rgb(229, 231, 235))
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()
}
