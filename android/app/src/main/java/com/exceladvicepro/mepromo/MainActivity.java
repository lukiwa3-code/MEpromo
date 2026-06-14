package com.exceladvicepro.mepromo;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public class MainActivity extends Activity {
    private static final String PREFS = "me_promo_settings";
    private static final String TOKEN_KEY = "github_token";
    private static final String PAGE_URL = "https://lukiwa3-code.github.io/MEpromo/";
    private static final String WORKFLOW_URL = "https://api.github.com/repos/lukiwa3-code/MEpromo/actions/workflows/cron_update_prices.yml/dispatches";
    private static final String ACTIONS_URL = "https://github.com/lukiwa3-code/MEpromo/actions/workflows/cron_update_prices.yml";

    private WebView webView;
    private TextView statusText;
    private ProgressBar progressBar;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        setupWebView();
        webView.loadUrl(PAGE_URL);
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(15, 23, 42));

        LinearLayout topBar = new LinearLayout(this);
        topBar.setOrientation(LinearLayout.VERTICAL);
        topBar.setPadding(18, 18, 18, 14);
        topBar.setBackgroundColor(Color.rgb(15, 23, 42));

        TextView title = new TextView(this);
        title.setText("ME Promo Tracker");
        title.setTextColor(Color.WHITE);
        title.setTextSize(20);
        title.setGravity(Gravity.START);
        topBar.addView(title);

        statusText = new TextView(this);
        statusText.setText("Gotowe. Strona załadowana z GitHub Pages.");
        statusText.setTextColor(Color.rgb(203, 213, 225));
        statusText.setTextSize(13);
        statusText.setPadding(0, 8, 0, 10);
        topBar.addView(statusText);

        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.HORIZONTAL);
        buttons.setGravity(Gravity.CENTER_VERTICAL);

        Button refreshButton = new Button(this);
        refreshButton.setText("Odśwież dane");
        refreshButton.setAllCaps(false);
        refreshButton.setOnClickListener(v -> triggerWorkflow());
        buttons.addView(refreshButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        Button reloadButton = new Button(this);
        reloadButton.setText("Odśwież stronę");
        reloadButton.setAllCaps(false);
        reloadButton.setOnClickListener(v -> webView.reload());
        buttons.addView(reloadButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        Button settingsButton = new Button(this);
        settingsButton.setText("Token");
        settingsButton.setAllCaps(false);
        settingsButton.setOnClickListener(v -> showTokenDialog());
        buttons.addView(settingsButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        topBar.addView(buttons);

        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setIndeterminate(true);
        progressBar.setVisibility(View.GONE);
        topBar.addView(progressBar, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        webView = new WebView(this);

        root.addView(topBar, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
        root.addView(webView, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));

        setContentView(root);
    }

    private void setupWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        webView.setWebViewClient(new WebViewClient());
    }

    private void triggerWorkflow() {
        String token = getStoredToken();
        if (token.isEmpty()) {
            showTokenDialog();
            Toast.makeText(this, "Najpierw wpisz GitHub token.", Toast.LENGTH_LONG).show();
            return;
        }

        progressBar.setVisibility(View.VISIBLE);
        statusText.setText("Uruchamiam workflow na GitHubie...");

        new Thread(() -> {
            try {
                URL url = new URL(WORKFLOW_URL);
                HttpURLConnection connection = (HttpURLConnection) url.openConnection();
                connection.setRequestMethod("POST");
                connection.setRequestProperty("Accept", "application/vnd.github+json");
                connection.setRequestProperty("Authorization", "Bearer " + token);
                connection.setRequestProperty("X-GitHub-Api-Version", "2022-11-28");
                connection.setRequestProperty("Content-Type", "application/json");
                connection.setDoOutput(true);

                byte[] body = "{\"ref\":\"main\"}".getBytes(StandardCharsets.UTF_8);
                try (OutputStream os = connection.getOutputStream()) {
                    os.write(body);
                }

                int code = connection.getResponseCode();
                if (code == 204) {
                    mainHandler.post(() -> {
                        progressBar.setVisibility(View.GONE);
                        statusText.setText("Workflow uruchomiony. Poczekaj 3-5 minut i odśwież stronę.");
                        Toast.makeText(this, "Odświeżanie uruchomione", Toast.LENGTH_LONG).show();
                    });
                    mainHandler.postDelayed(() -> webView.reload(), 240000);
                } else {
                    mainHandler.post(() -> {
                        progressBar.setVisibility(View.GONE);
                        statusText.setText("Nie udało się uruchomić workflow. HTTP " + code);
                        Toast.makeText(this, "GitHub API HTTP " + code, Toast.LENGTH_LONG).show();
                        openActionsPageFallback();
                    });
                }
            } catch (Exception ex) {
                mainHandler.post(() -> {
                    progressBar.setVisibility(View.GONE);
                    statusText.setText("Błąd: " + ex.getMessage());
                    Toast.makeText(this, "Błąd uruchamiania workflow", Toast.LENGTH_LONG).show();
                    openActionsPageFallback();
                });
            }
        }).start();
    }

    private void showTokenDialog() {
        EditText input = new EditText(this);
        input.setSingleLine(false);
        input.setMinLines(2);
        input.setText(getStoredToken());
        input.setHint("GitHub fine-grained token z uprawnieniem Actions: write dla repo MEpromo");

        new AlertDialog.Builder(this)
                .setTitle("GitHub token")
                .setMessage("Token jest zapisywany tylko lokalnie w telefonie. Nie zapisuj go w repozytorium.")
                .setView(input)
                .setPositiveButton("Zapisz", (dialog, which) -> {
                    String token = input.getText().toString().trim();
                    getPrefs().edit().putString(TOKEN_KEY, token).apply();
                    Toast.makeText(this, "Token zapisany lokalnie", Toast.LENGTH_SHORT).show();
                })
                .setNegativeButton("Anuluj", null)
                .setNeutralButton("Otwórz Actions", (dialog, which) -> openActionsPageFallback())
                .show();
    }

    private void openActionsPageFallback() {
        Intent browserIntent = new Intent(Intent.ACTION_VIEW, Uri.parse(ACTIONS_URL));
        startActivity(browserIntent);
    }

    private String getStoredToken() {
        return getPrefs().getString(TOKEN_KEY, "").trim();
    }

    private SharedPreferences getPrefs() {
        return getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
