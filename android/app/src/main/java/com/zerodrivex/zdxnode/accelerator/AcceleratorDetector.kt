package com.zerodrivex.zdxnode.accelerator

import android.app.ActivityManager
import android.content.Context

/** Conservative capability discovery; unavailable accelerators are never assumed. */
class AcceleratorDetector(private val context: Context) {
    fun gpu(): GpuCapability {
        val manager = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val gl = manager.deviceConfigurationInfo.glEsVersion
        val packageManager = context.packageManager
        val vulkan = packageManager.hasSystemFeature("android.hardware.vulkan.level") ||
            packageManager.hasSystemFeature("android.hardware.vulkan.version")
        return GpuCapability(vendor = null, model = null, vulkanSupported = vulkan, openGlVersion = gl)
    }

    fun npu(): NpuCapability {
        val available = context.packageManager.hasSystemFeature("android.hardware.neuralnetworks")
        val version = if (available && android.os.Build.VERSION.SDK_INT >= 27) 1 else 0
        return NpuCapability(available = available, nnapiVersion = version, acceleratorName = null)
    }
}
