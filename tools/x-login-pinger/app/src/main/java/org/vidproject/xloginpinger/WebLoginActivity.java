package org.vidproject.xloginpinger;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public final class WebLoginActivity extends Activity {
    private static final String EXTRA_URL = "url";
    private WebView webView;
    private TextView urlText;

    static Intent intent(Context context, String url) {
        Intent intent = new Intent(context, WebLoginActivity.class);
        intent.putExtra(EXTRA_URL, normalize(url));
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        return intent;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(buildView());
        configureWebView();
        String url = normalize(getIntent().getStringExtra(EXTRA_URL));
        urlText.setText(url);
        webView.loadUrl(url);
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }

    private LinearLayout buildView() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(16, 20, 24));

        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.VERTICAL);
        top.setPadding(dp(10), dp(8), dp(10), dp(8));
        root.addView(top, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        urlText = new TextView(this);
        urlText.setTextColor(Color.rgb(244, 247, 250));
        urlText.setTextSize(13);
        urlText.setSingleLine(true);
        top.addView(urlText);

        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.HORIZONTAL);
        top.addView(buttons);

        Button back = smallButton("Back");
        back.setOnClickListener(v -> {
            if (webView.canGoBack()) {
                webView.goBack();
            }
        });
        buttons.addView(back);

        Button reload = smallButton("Reload");
        reload.setOnClickListener(v -> webView.reload());
        buttons.addView(reload);

        Button external = smallButton("Browser");
        external.setOnClickListener(v -> startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(webView.getUrl()))));
        buttons.addView(external);

        Button done = smallButton("Done");
        done.setOnClickListener(v -> finish());
        buttons.addView(done);

        webView = new WebView(this);
        root.addView(webView, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            0,
            1f
        ));
        return root;
    }

    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setMediaPlaybackRequiresUserGesture(false);

        CookieManager cookieManager = CookieManager.getInstance();
        cookieManager.setAcceptCookie(true);
        cookieManager.setAcceptThirdPartyCookies(webView, true);

        webView.setWebChromeClient(new WebChromeClient());
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                urlText.setText(url);
                CookieManager.getInstance().flush();
            }
        });
    }

    private Button smallButton(String value) {
        Button button = new Button(this);
        button.setText(value);
        button.setAllCaps(false);
        button.setTextSize(12);
        return button;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static String normalize(String value) {
        if (value == null || value.trim().isEmpty()) {
            return SettingsStore.DEFAULT_LOGIN_URL;
        }
        String trimmed = value.trim();
        if (!trimmed.startsWith("http://") && !trimmed.startsWith("https://")) {
            return "https://" + trimmed;
        }
        return trimmed;
    }
}
