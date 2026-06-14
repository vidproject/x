package org.vidproject.xloginpinger;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

final class LoginStatusChecker {
    private LoginStatusChecker() {
    }

    static Result check(String statusUrl, String fallbackLoginUrl) {
        if (isBlank(statusUrl)) {
            return new Result(false, false, fallbackLoginUrl, "No status URL configured.", false);
        }

        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(statusUrl).openConnection();
            connection.setConnectTimeout(10_000);
            connection.setReadTimeout(10_000);
            connection.setRequestMethod("GET");
            connection.setRequestProperty("Accept", "application/json,text/plain,*/*");
            connection.setUseCaches(false);

            int code = connection.getResponseCode();
            String body = readBody(code >= 400 ? connection.getErrorStream() : connection.getInputStream());
            Result parsed = parseBody(code, body, fallbackLoginUrl);
            return new Result(
                parsed.loginRequired,
                parsed.streamDown,
                parsed.loginUrl,
                parsed.message,
                true
            );
        } catch (Exception error) {
            return new Result(
                false,
                true,
                fallbackLoginUrl,
                "Status endpoint unreachable: " + safeMessage(error),
                true
            );
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    static Result parseBody(int code, String body, String fallbackLoginUrl) {
        if (code == HttpURLConnection.HTTP_UNAUTHORIZED || code == HttpURLConnection.HTTP_FORBIDDEN) {
            return new Result(
                true,
                false,
                fallbackLoginUrl,
                "Status endpoint returned HTTP " + code + ": login required.",
                true
            );
        }

        boolean serverDown = code >= 500;
        String trimmed = body == null ? "" : body.trim();
        if (!trimmed.isEmpty() && trimmed.startsWith("{")) {
            try {
                JSONObject json = new JSONObject(trimmed);
                boolean loginRequired = firstBoolean(
                    json,
                    "login_required",
                    "loginRequired",
                    "needs_login",
                    "needsLogin",
                    "reauth_required",
                    "requires_login",
                    "x_login_required",
                    "xLoginRequired"
                );
                boolean streamDown = serverDown || firstBoolean(
                    json,
                    "stream_down",
                    "streamDown",
                    "capture_down",
                    "captureDown",
                    "capture_stalled",
                    "captureStalled",
                    "stream_stalled",
                    "streamStalled",
                    "down",
                    "offline",
                    "unhealthy"
                );
                Boolean ok = firstNullableBoolean(json, "ok", "healthy");
                Boolean streamOk = firstNullableBoolean(
                    json,
                    "stream_ok",
                    "streamOk",
                    "capture_ok",
                    "captureOk"
                );
                if (Boolean.FALSE.equals(ok) || Boolean.FALSE.equals(streamOk)) {
                    streamDown = true;
                }

                StatusHints hints = statusHints(firstRawString(
                    json,
                    "status",
                    "state",
                    "health",
                    "stream_status",
                    "streamStatus",
                    "capture_status",
                    "captureStatus"
                ));
                loginRequired = loginRequired || hints.loginRequired;
                streamDown = streamDown || hints.streamDown;

                String url = firstString(json, fallbackLoginUrl, "login_url", "loginUrl", "url");
                String message = firstString(
                    json,
                    defaultMessage(loginRequired, streamDown, code),
                    "message",
                    "detail",
                    "reason",
                    "error"
                );
                return new Result(loginRequired, streamDown, url, message, true);
            } catch (JSONException ignored) {
                // Fall through to text parsing.
            }
        }

        String lower = trimmed.toLowerCase(Locale.US);
        boolean loginRequired = lower.contains("login_required=true")
            || lower.contains("\"login_required\":true")
            || lower.contains("x-login-required")
            || lower.contains("reauth_required")
            || lower.contains("needs login")
            || lower.contains("login needed")
            || lower.contains("log back in");
        boolean streamDown = serverDown
            || lower.contains("stream_down=true")
            || lower.contains("\"stream_down\":true")
            || lower.contains("capture_down=true")
            || lower.contains("\"capture_down\":true")
            || lower.contains("stream_status=down")
            || lower.contains("capture_status=down")
            || lower.contains("stream down")
            || lower.contains("capture stream down")
            || lower.contains("capture down")
            || lower.contains("stream offline")
            || lower.contains("capture offline")
            || lower.contains("stream stalled")
            || lower.contains("capture stalled")
            || lower.equals("down")
            || lower.equals("offline")
            || lower.equals("unhealthy")
            || lower.equals("stalled");
        String message = trimmed.isEmpty() ? "Status endpoint returned HTTP " + code + "." : cap(trimmed, 220);
        if (trimmed.isEmpty() && (loginRequired || streamDown)) {
            message = defaultMessage(loginRequired, streamDown, code);
        }
        return new Result(loginRequired, streamDown, fallbackLoginUrl, message, true);
    }

    private static boolean firstBoolean(JSONObject json, String... keys) {
        Boolean result = firstNullableBoolean(json, keys);
        return result != null && result;
    }

    private static Boolean firstNullableBoolean(JSONObject json, String... keys) {
        for (String key : keys) {
            if (!json.has(key)) {
                continue;
            }
            Object value = json.opt(key);
            if (value instanceof Boolean) {
                return (Boolean) value;
            }
            if (value instanceof Number) {
                return ((Number) value).intValue() != 0;
            }
            String text = String.valueOf(value).trim().toLowerCase(Locale.US);
            if ("true".equals(text) || "yes".equals(text) || "1".equals(text)) {
                return true;
            }
            if ("false".equals(text) || "no".equals(text) || "0".equals(text)) {
                return false;
            }
        }
        return null;
    }

    private static String firstString(JSONObject json, String fallback, String... keys) {
        for (String key : keys) {
            String value = json.optString(key, "");
            if (!isBlank(value)) {
                return cap(value.trim(), 220);
            }
        }
        return fallback;
    }

    private static String firstRawString(JSONObject json, String... keys) {
        for (String key : keys) {
            String value = json.optString(key, "");
            if (!isBlank(value)) {
                return value.trim();
            }
        }
        return "";
    }

    private static StatusHints statusHints(String value) {
        String text = value == null ? "" : value.trim().toLowerCase(Locale.US);
        boolean loginRequired = text.equals("login_required")
            || text.equals("needs_login")
            || text.equals("reauth_required")
            || text.equals("auth-error")
            || text.equals("auth_error")
            || text.contains("login required")
            || text.contains("needs login");
        boolean streamDown = text.equals("down")
            || text.equals("offline")
            || text.equals("unhealthy")
            || text.equals("stalled")
            || text.equals("capture_stalled")
            || text.equals("capture-stalled")
            || text.equals("stream_stalled")
            || text.equals("stream-stalled")
            || text.equals("failed")
            || text.equals("error")
            || text.equals("stopped")
            || text.equals("capture_down")
            || text.equals("capture-down")
            || text.equals("stream_down")
            || text.equals("stream-down")
            || text.contains("stream down")
            || text.contains("capture down")
            || text.contains("stream stalled")
            || text.contains("capture stalled");
        return new StatusHints(loginRequired, streamDown);
    }

    private static String defaultMessage(boolean loginRequired, boolean streamDown, int code) {
        if (loginRequired && streamDown) {
            return "X capture stream is down and login is needed.";
        }
        if (loginRequired) {
            return "X login is needed.";
        }
        if (streamDown) {
            return "X capture stream is down.";
        }
        return code >= 200 && code < 300
            ? "Session and stream look okay."
            : "Status endpoint returned HTTP " + code + ".";
    }

    private static String readBody(InputStream stream) throws IOException {
        if (stream == null) {
            return "";
        }
        StringBuilder builder = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            char[] buffer = new char[2048];
            int read;
            while ((read = reader.read(buffer)) != -1 && builder.length() < 8192) {
                builder.append(buffer, 0, read);
            }
        }
        return builder.toString();
    }

    private static boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }

    private static String safeMessage(Exception error) {
        String message = error.getMessage();
        return isBlank(message) ? error.getClass().getSimpleName() : cap(message, 220);
    }

    private static String cap(String value, int max) {
        if (value.length() <= max) {
            return value;
        }
        return value.substring(0, max - 3) + "...";
    }

    private static final class StatusHints {
        final boolean loginRequired;
        final boolean streamDown;

        StatusHints(boolean loginRequired, boolean streamDown) {
            this.loginRequired = loginRequired;
            this.streamDown = streamDown;
        }
    }

    static final class Result {
        final boolean loginRequired;
        final boolean streamDown;
        final String loginUrl;
        final String message;
        final boolean configured;

        Result(
            boolean loginRequired,
            boolean streamDown,
            String loginUrl,
            String message,
            boolean configured
        ) {
            this.loginRequired = loginRequired;
            this.streamDown = streamDown;
            this.loginUrl = isBlank(loginUrl) ? SettingsStore.DEFAULT_LOGIN_URL : loginUrl;
            this.message = isBlank(message) ? "No status message." : message;
            this.configured = configured;
        }

        boolean attentionRequired() {
            return loginRequired || streamDown;
        }

        String alertTitle() {
            if (loginRequired && streamDown) {
                return "X capture needs attention";
            }
            if (loginRequired) {
                return "Log back into X";
            }
            if (streamDown) {
                return "X capture stream is down";
            }
            return "X capture monitor";
        }
    }
}
