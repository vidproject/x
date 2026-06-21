package org.vidproject.xloginpinger;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

final class Notifier {
    static final int MONITOR_NOTIFICATION_ID = 1001;
    private static final int LOGIN_NOTIFICATION_ID = 1002;
    private static final String CHANNEL_MONITOR = "monitor";
    private static final String CHANNEL_ALERTS = "alerts";

    private Notifier() {
    }

    static void ensureChannels(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null) {
            return;
        }
        NotificationChannel monitor = new NotificationChannel(
            CHANNEL_MONITOR,
            "X login monitor",
            NotificationManager.IMPORTANCE_LOW
        );
        monitor.setDescription("Persistent monitor status while polling is active.");
        manager.createNotificationChannel(monitor);

        NotificationChannel alerts = new NotificationChannel(
            CHANNEL_ALERTS,
            "X login alerts",
            NotificationManager.IMPORTANCE_HIGH
        );
        alerts.setDescription("Alerts when the capture stream is down or X needs a fresh login.");
        manager.createNotificationChannel(alerts);
    }

    static Notification monitorNotification(Context context, String text) {
        Intent openIntent = new Intent(context, MainActivity.class);
        PendingIntent openPending = PendingIntent.getActivity(
            context,
            10,
            openIntent,
            flags()
        );

        Intent stopIntent = new Intent(context, MonitorService.class);
        stopIntent.setAction(MonitorService.ACTION_STOP);
        PendingIntent stopPending = PendingIntent.getService(
            context,
            11,
            stopIntent,
            flags()
        );

        return builder(context, CHANNEL_MONITOR)
            .setSmallIcon(R.drawable.ic_stat_login)
            .setContentTitle("X login monitor running")
            .setContentText(text)
            .setContentIntent(openPending)
            .setOngoing(true)
            .addAction(R.drawable.ic_stat_login, "Stop", stopPending)
            .build();
    }

    static void showAttentionNeeded(Context context, String title, String message, String loginUrl) {
        ensureChannels(context);
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) {
            return;
        }
        Intent loginIntent = WebLoginActivity.intent(context, loginUrl);
        PendingIntent loginPending = PendingIntent.getActivity(
            context,
            20,
            loginIntent,
            flags()
        );
        Notification notification = builder(context, CHANNEL_ALERTS)
            .setSmallIcon(R.drawable.ic_stat_login)
            .setContentTitle(title)
            .setContentText(message)
            .setStyle(new Notification.BigTextStyle().bigText(message))
            .setContentIntent(loginPending)
            .setAutoCancel(true)
            .addAction(R.drawable.ic_stat_login, "Open login", loginPending)
            .build();
        manager.notify(LOGIN_NOTIFICATION_ID, notification);
    }

    static void updateMonitor(Context context, String message) {
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null) {
            manager.notify(MONITOR_NOTIFICATION_ID, monitorNotification(context, message));
        }
    }

    private static Notification.Builder builder(Context context, String channelId) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            return new Notification.Builder(context, channelId);
        }
        return new Notification.Builder(context);
    }

    private static int flags() {
        int result = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            result |= PendingIntent.FLAG_IMMUTABLE;
        }
        return result;
    }
}
