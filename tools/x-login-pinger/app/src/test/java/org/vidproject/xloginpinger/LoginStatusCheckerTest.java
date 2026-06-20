package org.vidproject.xloginpinger;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class LoginStatusCheckerTest {
    @Test
    public void parseJsonLoginRequired() {
        LoginStatusChecker.Result result = LoginStatusChecker.parseBody(
            200,
            "{\"login_required\":true,\"message\":\"fresh login needed\"}",
            SettingsStore.DEFAULT_LOGIN_URL
        );

        assertTrue(result.attentionRequired());
        assertTrue(result.loginRequired);
        assertFalse(result.streamDown);
    }

    @Test
    public void parseJsonStreamDown() {
        LoginStatusChecker.Result result = LoginStatusChecker.parseBody(
            200,
            "{\"stream_down\":true,\"login_url\":\"https://x.com/i/flow/login\"}",
            SettingsStore.DEFAULT_LOGIN_URL
        );

        assertTrue(result.attentionRequired());
        assertFalse(result.loginRequired);
        assertTrue(result.streamDown);
    }

    @Test
    public void parseStatusStringAsStreamDown() {
        LoginStatusChecker.Result result = LoginStatusChecker.parseBody(
            200,
            "{\"status\":\"capture_stalled\"}",
            SettingsStore.DEFAULT_LOGIN_URL
        );

        assertTrue(result.attentionRequired());
        assertTrue(result.streamDown);
    }

    @Test
    public void parseServerErrorAsStreamDown() {
        LoginStatusChecker.Result result = LoginStatusChecker.parseBody(
            503,
            "",
            SettingsStore.DEFAULT_LOGIN_URL
        );

        assertTrue(result.attentionRequired());
        assertTrue(result.streamDown);
    }

    @Test
    public void parseOkJsonWithoutAlert() {
        LoginStatusChecker.Result result = LoginStatusChecker.parseBody(
            200,
            "{\"ok\":true,\"status\":\"ok\"}",
            SettingsStore.DEFAULT_LOGIN_URL
        );

        assertFalse(result.attentionRequired());
    }
}
