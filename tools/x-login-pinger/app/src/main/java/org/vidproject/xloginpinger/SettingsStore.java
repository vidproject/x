package org.vidproject.xloginpinger;

import android.content.Context;
import android.content.SharedPreferences;

final class SettingsStore {
    static final String DEFAULT_LOGIN_URL = "https://x.com/i/flow/login";
    private static final String PREFS = "x_login_pinger";
    private static final String KEY_STATUS_URL = "status_url";
    private static final String KEY_LOGIN_URL = "login_url";
    private static final String KEY_POLL_MINUTES = "poll_minutes";
    private static final String KEY_MONITOR_ENABLED = "monitor_enabled";
    private static final String KEY_LAST_NOTIFIED_AT = "last_notified_at";

    private SettingsStore() {
    }

    static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static String statusUrl(Context context) {
        return prefs(context).getString(KEY_STATUS_URL, "");
    }

    static void setStatusUrl(Context context, String value) {
        prefs(context).edit().putString(KEY_STATUS_URL, clean(value)).apply();
    }

    static String loginUrl(Context context) {
        String value = prefs(context).getString(KEY_LOGIN_URL, DEFAULT_LOGIN_URL);
        if (value == null || value.trim().isEmpty()) {
            return DEFAULT_LOGIN_URL;
        }
        return value.trim();
    }

    static void setLoginUrl(Context context, String value) {
        String cleaned = clean(value);
        if (cleaned.isEmpty()) {
            cleaned = DEFAULT_LOGIN_URL;
        }
        prefs(context).edit().putString(KEY_LOGIN_URL, cleaned).apply();
    }

    static int pollMinutes(Context context) {
        return Math.max(1, prefs(context).getInt(KEY_POLL_MINUTES, 5));
    }

    static void setPollMinutes(Context context, int value) {
        prefs(context).edit().putInt(KEY_POLL_MINUTES, Math.max(1, value)).apply();
    }

    static boolean monitorEnabled(Context context) {
        return prefs(context).getBoolean(KEY_MONITOR_ENABLED, false);
    }

    static void setMonitorEnabled(Context context, boolean value) {
        prefs(context).edit().putBoolean(KEY_MONITOR_ENABLED, value).apply();
    }

    static long lastNotifiedAt(Context context) {
        return prefs(context).getLong(KEY_LAST_NOTIFIED_AT, 0L);
    }

    static void setLastNotifiedAt(Context context, long value) {
        prefs(context).edit().putLong(KEY_LAST_NOTIFIED_AT, value).apply();
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
