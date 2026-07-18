package com.zerodrivex.zdxnode

import android.content.Context
import android.os.BatteryManager
import android.os.Build
import android.app.ActivityManager

/**
 * Collects safe device information for ZDX node advertisement.
 */
class DeviceCapabilityCollector(private val context: Context) {

    fun collect(): NodeCapability {
        val manager = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val memory = manager.memoryClass

        val batteryManager = context.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        val battery = batteryManager.getIntProperty(
            BatteryManager.BATTERY_PROPERTY_CAPACITY
        )

        return NodeCapability(
            nodeId = Build.SERIAL.takeIf { it != Build.UNKNOWN } ?: "android-node",
            cpu = Build.HARDWARE,
            memoryMb = memory,
            charging = false,
            batteryPercent = battery
        )
    }
}
