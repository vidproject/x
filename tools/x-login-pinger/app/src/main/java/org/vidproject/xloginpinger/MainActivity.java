package org.vidproject.xloginpinger;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

public final class MainActivity extends Activity {
    private EditText statusUrlField;
    private EditText loginUrlField;
    private EditText pollMinutesField;
    private TextView statusText;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Notifier.ensureChannels(this);
        requestNotificationPermission();
        setContentView(buildView());
        loadSettings();
    }

    @Override
    protected void onResume() {
        super.onResume();
        updateMonitorText();
    }

    private ScrollView buildView() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(Color.rgb(16, 20, 24));

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(18), dp(20), dp(18), dp(20));
        scroll.addView(root, new ScrollView.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        TextView title = text("X Capture Monitor", 24, Color.rgb(244, 247, 250));
        title.setGravity(Gravity.START);
        root.addView(title);

        TextView subtitle = text(
            "Polls a status endpoint and opens a WebView when the X stream is down or a fresh login is needed. It does not read or export cookies.",
            14,
            Color.rgb(180, 192, 202)
        );
        subtitle.setPadding(0, dp(6), 0, dp(18));
        root.addView(subtitle);

        statusUrlField = input("Status URL");
        root.addView(label("Status URL"));
        root.addView(statusUrlField);

        loginUrlField = input("Login URL");
        root.addView(label("Fallback login URL"));
        root.addView(loginUrlField);

        pollMinutesField = input("Poll minutes");
        pollMinutesField.setInputType(InputType.TYPE_CLASS_NUMBER);
        root.addView(label("Poll interval, minutes"));
        root.addView(pollMinutesField);

        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.VERTICAL);
        buttons.setPadding(0, dp(14), 0, dp(14));
        root.addView(buttons);

        Button save = button("Save settings");
        save.setOnClickListener(v -> saveSettings());
        buttons.addView(save);

        Button check = button("Check now");
        check.setOnClickListener(v -> checkNow());
        buttons.addView(check);

        Button login = button("Open login");
        login.setOnClickListener(v -> {
            saveSettings();
            startActivity(WebLoginActivity.intent(this, SettingsStore.loginUrl(this)));
        });
        buttons.addView(login);

        Button start = button("Start monitor");
        start.setOnClickListener(v -> {
            saveSettings();
            Intent intent = new Intent(this, MonitorService.class);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(intent);
            } else {
                startService(intent);
            }
            updateMonitorText();
        });
        buttons.addView(start);

        Button stop = button("Stop monitor");
        stop.setOnClickListener(v -> {
            stopService(new Intent(this, MonitorService.class));
            SettingsStore.setMonitorEnabled(this, false);
            updateMonitorText();
        });
        buttons.addView(stop);

        statusText = text("", 14, Color.rgb(244, 247, 250));
        statusText.setPadding(0, dp(10), 0, 0);
        root.addView(statusText);
        return scroll;
    }

    private void loadSettings() {
        statusUrlField.setText(SettingsStore.statusUrl(this));
        loginUrlField.setText(SettingsStore.loginUrl(this));
        pollMinutesField.setText(String.valueOf(SettingsStore.pollMinutes(this)));
        updateMonitorText();
    }

    private void saveSettings() {
        SettingsStore.setStatusUrl(this, statusUrlField.getText().toString());
        SettingsStore.setLoginUrl(this, loginUrlField.getText().toString());
        int minutes = 5;
        try {
            minutes = Integer.parseInt(pollMinutesField.getText().toString().trim());
        } catch (NumberFormatException ignored) {
            // Keep default.
        }
        SettingsStore.setPollMinutes(this, minutes);
        statusText.setText("Saved. " + monitorStateText());
    }

    private void checkNow() {
        saveSettings();
        statusText.setText("Checking...");
        new Thread(() -> {
            LoginStatusChecker.Result result = LoginStatusChecker.check(
                SettingsStore.statusUrl(this),
                SettingsStore.loginUrl(this)
            );
            if (result.attentionRequired()) {
                Notifier.showAttentionNeeded(this, result.alertTitle(), result.message, result.loginUrl);
            }
            runOnUiThread(() -> statusText.setText(formatResult(result)));
        }, "x-login-check-now").start();
    }

    private void updateMonitorText() {
        if (statusText != null) {
            statusText.setText(monitorStateText());
        }
    }

    private String monitorStateText() {
        return SettingsStore.monitorEnabled(this) ? "Monitor marked running." : "Monitor stopped.";
    }

    private String formatResult(LoginStatusChecker.Result result) {
        String prefix = result.attentionRequired() ? "Attention needed: " : "No alert: ";
        return prefix + result.message
            + "\nStream down: " + result.streamDown
            + "\nLogin needed: " + result.loginRequired
            + "\nLogin URL: " + result.loginUrl;
    }

    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33
            && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[] { Manifest.permission.POST_NOTIFICATIONS }, 100);
        }
    }

    private TextView label(String value) {
        TextView label = text(value, 13, Color.rgb(180, 192, 202));
        label.setPadding(0, dp(10), 0, dp(4));
        return label;
    }

    private TextView text(String value, int sp, int color) {
        TextView text = new TextView(this);
        text.setText(value);
        text.setTextSize(sp);
        text.setTextColor(color);
        text.setLineSpacing(0f, 1.1f);
        return text;
    }

    private EditText input(String hint) {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setHint(hint);
        input.setTextColor(Color.rgb(244, 247, 250));
        input.setHintTextColor(Color.rgb(132, 145, 156));
        input.setTextSize(15);
        input.setSelectAllOnFocus(false);
        input.setPadding(dp(10), dp(8), dp(10), dp(8));
        input.setBackgroundColor(Color.rgb(24, 32, 40));
        return input;
    }

    private Button button(String value) {
        Button button = new Button(this);
        button.setText(value);
        button.setAllCaps(false);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        params.setMargins(0, dp(4), 0, dp(4));
        button.setLayoutParams(params);
        return button;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
