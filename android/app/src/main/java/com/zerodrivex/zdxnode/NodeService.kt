package com.zerodrivex.zdxnode

import android.app.Service
import android.content.Intent
import android.os.IBinder

/**
 * Long running Android node lifecycle service.
 * Network transport integration is added separately.
 */
class NodeService : Service() {

    override fun onBind(intent: Intent?): IBinder? {
        return null
    }

    override fun onStartCommand(
        intent: Intent?,
        flags: Int,
        startId: Int
    ): Int {
        return START_STICKY
    }
}
