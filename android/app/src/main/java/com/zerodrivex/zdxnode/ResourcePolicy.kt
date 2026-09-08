package com.zerodrivex.zdxnode

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager

/** Persisted compute participation settings for a mobile node. */
data class ResourcePolicy(
    val enabled: Boolean = false,
    val idleOnly: Boolean = true,
    val memoryLimitMb: Int = 256,
    val minFreeMemoryMb: Int = 512,
    val requireCharging: Boolean = true
)

/** SharedPreferences-backed settings; safe defaults never consume resources. */
class ResourcePolicyStore(context: Context) {
    private val preferences = context.getSharedPreferences("zdx_resource_policy", Context.MODE_PRIVATE)

    fun load(): ResourcePolicy = ResourcePolicy(
        enabled = preferences.getBoolean("enabled", false),
        idleOnly = preferences.getBoolean("idle_only", true),
        memoryLimitMb = preferences.getInt("memory_limit_mb", 256),
        minFreeMemoryMb = preferences.getInt("min_free_memory_mb", 512),
        requireCharging = preferences.getBoolean("require_charging", true)
    )

    fun save(policy: ResourcePolicy) {
        require(policy.memoryLimitMb > 0)
        require(policy.minFreeMemoryMb >= 0)
        preferences.edit()
            .putBoolean("enabled", policy.enabled)
            .putBoolean("idle_only", policy.idleOnly)
            .putInt("memory_limit_mb", policy.memoryLimitMb)
            .putInt("min_free_memory_mb", policy.minFreeMemoryMb)
            .putBoolean("require_charging", policy.requireCharging)
            .apply()
    }
}

data class AndroidResourceSnapshot(
    val availableMemoryMb: Int,
    val charging: Boolean,
    val deviceIdle: Boolean
)

/** Admission gate used before any Android compute work is started. */
class AndroidResourceAdmission(private val context: Context) {
    fun snapshot(): AndroidResourceSnapshot {
        val activityManager = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val memory = ActivityManager.MemoryInfo()
        activityManager.getMemoryInfo(memory)
        val batteryIntent = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val status = batteryIntent?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val charging = status == BatteryManager.BATTERY_STATUS_CHARGING ||
            status == BatteryManager.BATTERY_STATUS_FULL
        val powerManager = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        val deviceIdle = Build.VERSION.SDK_INT < Build.VERSION_CODES.M || powerManager.isDeviceIdleMode
        return AndroidResourceSnapshot(
            availableMemoryMb = (memory.availMem / (1024 * 1024)).toInt(),
            charging = charging,
            deviceIdle = deviceIdle
        )
    }

    fun canRun(policy: ResourcePolicy): Boolean {
        if (!policy.enabled) return false
        val current = snapshot()
        if (policy.requireCharging && !current.charging) return false
        if (policy.idleOnly && !current.deviceIdle) return false
        return current.availableMemoryMb - policy.minFreeMemoryMb >= policy.memoryLimitMb
    }
}
