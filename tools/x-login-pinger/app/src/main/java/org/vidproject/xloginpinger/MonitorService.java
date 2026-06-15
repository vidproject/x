package org.vidproject.xloginpinger;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;
import android.os.SystemClock;

public final class MonitorService extends Service {
    static final String ACTION_STOP = "org.vidproject.xloginpinger.STOP";
    private static final long NOTIFY_THROTTLE_MS = 15L * 60L * 1000L;

    private volatile boolean running;
    private Thread worker;

    @Override
    public void onCreate() {
        super.onCreate();
        Notifier.ensureChannels(this);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopSelf();
            return START_NOT_STICKY;
        }

        SettingsStore.setMonitorEnabled(this, true);
        startForeground(
            Notifier.MONITOR_NOTIFICATION_ID,
            Notifier.monitorNotification(this, "Polling every " + SettingsStore.pollMinutes(this) + " minute(s).")
        );
        startWorker();
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        SettingsStore.setMonitorEnabled(this, false);
        if (worker != null) {
            worker.interrupt();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private synchronized void startWorker() {
        if (worker != null && worker.isAlive()) {
            return;
        }
        running = true;
        worker = new Thread(this::runLoop, "x-login-monitor");
        worker.start();
    }

    private void runLoop() {
        while (running) {
            String statusUrl = SettingsStore.statusUrl(this);
            String fallbackLoginUrl = SettingsStore.loginUrl(this);
            LoginStatusChecker.Result result = LoginStatusChecker.check(statusUrl, fallbackLoginUrl);
            if (!result.configured) {
                Notifier.updateMonitor(this, "No status URL configured.");
            } else if (result.attentionRequired()) {
                Notifier.updateMonitor(this, result.alertTitle() + ".");
                maybeNotify(result);
            } else {
                Notifier.updateMonitor(this, "Last check: " + result.message);
            }
            sleepUntilNextPoll();
        }
    }

    private void maybeNotify(LoginStatusChecker.Result result) {
        long now = System.currentTimeMillis();
        long last = SettingsStore.lastNotifiedAt(this);
        if (now - last < NOTIFY_THROTTLE_MS) {
            return;
        }
        SettingsStore.setLastNotifiedAt(this, now);
        Notifier.showAttentionNeeded(this, result.alertTitle(), result.message, result.loginUrl);
    }

    private void sleepUntilNextPoll() {
        long remaining = Math.max(1, SettingsStore.pollMinutes(this)) * 60L * 1000L;
        while (running && remaining > 0L) {
            long chunk = Math.min(remaining, 5_000L);
            SystemClock.sleep(chunk);
            remaining -= chunk;
        }
    }
}
