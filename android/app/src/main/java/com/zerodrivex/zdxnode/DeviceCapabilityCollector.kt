package com.zerodrivex.zdxnode

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import com.zerodrivex.zdxnode.accelerator.AcceleratorDetector

/** Collects only local, non-secret device facts for dynamic admission. */
class DeviceCapabilityCollector(private val context: Context) {
    fun collect(): NodeCapability {
        val manager = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val memoryInfo = ActivityManager.MemoryInfo()
        manager.getMemoryInfo(memoryInfo)
        val batteryManager = context.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        val battery = batteryManager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        val batteryIntent = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val status = batteryIntent?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val accelerators = AcceleratorDetector(context)
        return NodeCapability(
            nodeId = Build.SERIAL.takeIf { it != Build.UNKNOWN } ?: "android-node",
            cpu = Build.HARDWARE,
            memoryMb = (memoryInfo.totalMem / (1024 * 1024)).toInt(),
            availableMemoryMb = (memoryInfo.availMem / (1024 * 1024)).toInt(),
            charging = status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL,
            batteryPercent = battery,
            gpu = accelerators.gpu(),
            npu = accelerators.npu()
        )
    }
}
