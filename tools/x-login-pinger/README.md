# X Capture Monitor

Tiny Android helper for X capture runs. It polls a status endpoint, sends an
Android notification when the endpoint says the capture stream is down or a
fresh login is needed, and opens a WebView to the relevant X login URL.

The app does not read, display, export, or upload WebView cookies. The WebView is
only an input surface for the user.

## Status Endpoint

Configure the app with a URL that returns either JSON or text.

Preferred JSON:

```json
{
  "login_required": true,
  "stream_down": false,
  "message": "The X skim worker needs a fresh login.",
  "login_url": "https://x.com/i/flow/login"
}
```

Recognized login-needed boolean keys:

- `login_required`
- `loginRequired`
- `needs_login`
- `needsLogin`
- `reauth_required`
- `requires_login`
- `x_login_required`

Recognized stream/capture-down boolean keys:

- `stream_down`
- `streamDown`
- `capture_down`
- `captureDown`
- `capture_stalled`
- `captureStalled`
- `stream_stalled`
- `streamStalled`
- `down`
- `offline`
- `unhealthy`

Recognized healthy boolean keys where `false` means alert:

- `ok`
- `healthy`
- `stream_ok`
- `streamOk`
- `capture_ok`
- `captureOk`

Recognized status string keys:

- `status`
- `state`
- `health`
- `stream_status`
- `streamStatus`
- `capture_status`
- `captureStatus`

Values like `down`, `offline`, `unhealthy`, `stalled`,
`capture_stalled`, `login_required`, or `needs_login` trigger an alert.

Recognized login URL keys:

- `login_url`
- `loginUrl`
- `url`

HTTP `401` or `403` is treated as login-needed. HTTP `5xx` is treated as
capture-stream-down. Plain text containing `login_required=true`,
`x-login-required`, `reauth_required`, `needs login`, or `log back in` is treated
as login-needed. Plain text containing `stream_down=true`, `capture_down=true`,
`stream down`, `capture down`, `stream offline`, or `capture stalled` is treated
as capture-stream-down.

If the configured endpoint is unreachable, the app treats that as a
capture-stream-down alert instead of silent failure.

## Build

From this directory:

```powershell
.\gradlew.bat clean testDebugUnitTest assembleDebug
```

APK:

`app/build/outputs/apk/debug/x-login-pinger-debug.apk`

The GitHub Actions workflow `.github/workflows/x-login-pinger.yml` builds the
same debug APK, uploads it as `x-login-pinger-debug-apk`, and publishes a direct
`.apk` release asset named `x-login-pinger-debug.apk`.

GitHub Actions artifacts download as ZIP files. If Android says "There was a
problem parsing the package," make sure the file being opened ends in `.apk`;
either extract the artifact ZIP first or use the release asset directly.
