package com.zerodrivex.zdxnode

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

data class WorkEvent(
    val timestamp: Long,
    val type: String,
    val message: String,
    val taskId: String? = null,
    val direction: String? = null
)

class WorkEventStore(context: Context) {
    private val preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun append(type: String, message: String, taskId: String? = null, direction: String? = null) {
        synchronized(LOCK) {
            val events = loadMutable()
            events.add(JSONObject()
                .put("timestamp", System.currentTimeMillis())
                .put("type", type.take(40))
                .put("message", message.replace(Regex("[\\r\\n]"), " ").take(500))
                .also {
                    if (!taskId.isNullOrBlank()) it.put("task_id", taskId.take(160))
                    if (!direction.isNullOrBlank()) it.put("direction", direction.take(24))
                })
            while (events.size > MAX_EVENTS) events.removeAt(0)
            val array = JSONArray()
            events.forEach { array.put(it) }
            preferences.edit().putString(KEY_EVENTS, array.toString()).apply()
        }
    }

    fun recent(limit: Int = 80): List<WorkEvent> = synchronized(LOCK) {
        val values = loadMutable().takeLast(limit.coerceIn(1, MAX_EVENTS))
        values.map {
            WorkEvent(
                timestamp = it.optLong("timestamp"),
                type = it.optString("type"),
                message = it.optString("message"),
                taskId = it.optString("task_id").takeIf(String::isNotBlank),
                direction = it.optString("direction").takeIf(String::isNotBlank)
            )
        }
    }

    fun consoleText(limit: Int = 80): String {
        val formatter = SimpleDateFormat("HH:mm:ss", Locale.US)
        val events = recent(limit)
        if (events.isEmpty()) return "No work events yet."
        return events.joinToString("\n") { event ->
            val time = formatter.format(Date(event.timestamp))
            val task = event.taskId?.let { " [$it]" }.orEmpty()
            val direction = event.direction?.let { " <$it>" }.orEmpty()
            "$time ${event.type}$direction$task ${event.message}"
        }
    }

    fun clear() {
        synchronized(LOCK) {
            preferences.edit().remove(KEY_EVENTS).apply()
        }
    }

    private fun loadMutable(): MutableList<JSONObject> {
        val raw = preferences.getString(KEY_EVENTS, null) ?: return mutableListOf()
        return try {
            val array = JSONArray(raw)
            MutableList(array.length()) { index -> array.getJSONObject(index) }
        } catch (_: Exception) {
            mutableListOf()
        }
    }

    companion object {
        private const val PREFS = "zdx_work_events"
        private const val KEY_EVENTS = "events"
        private const val MAX_EVENTS = 250
        private val LOCK = Any()
    }
}
