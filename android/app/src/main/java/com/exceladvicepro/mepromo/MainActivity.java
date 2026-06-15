package com.exceladvicepro.mepromo;

import android.app.*;
import android.os.*;
import android.content.*;
import android.graphics.*;
import android.graphics.drawable.*;
import android.net.*;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.text.*;
import java.util.*;

public class MainActivity extends Activity {
    private static final String PREFS = "prefs";
    private static final String KEY = "gh_key";
    private static final String DATA_URL = "https://api.github.com/repos/lukiwa3-code/MEpromo/contents/data/latest_prices.json?ref=main";
    private static final String RUN_URL = "https://api.github.com/repos/lukiwa3-code/MEpromo/actions/workflows/cron_update_prices.yml/dispatches";
    private static final String ACTIONS_URL = "https://github.com/lukiwa3-code/MEpromo/actions/workflows/cron_update_prices.yml";

    private final Handler ui = new Handler(Looper.getMainLooper());
    private LinearLayout list;
    private TextView status;
    private TextView summary;
    private ProgressBar bar;
    private EditText search;
    private JSONArray data = new JSONArray();
    private boolean openedGithubActions = false;
    private long lastLoadMillis = 0;
    private final NumberFormat pln = NumberFormat.getCurrencyInstance(new Locale("pl", "PL"));

    @Override public void onCreate(Bundle b) {
        super.onCreate(b);
        buildScreen();
        loadData();
    }

    @Override protected void onResume() {
        super.onResume();
        if (openedGithubActions) {
            openedGithubActions = false;
            status.setText("Wróciłeś z GitHuba. Pobieram najnowsze dane...");
            ui.postDelayed(() -> loadData(), 1500);
            ui.postDelayed(() -> loadData(), 90000);
            ui.postDelayed(() -> loadData(), 240000);
        } else if (System.currentTimeMillis() - lastLoadMillis > 300000) {
            loadData();
        }
    }

    private void buildScreen() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(15, 23, 42));

        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.VERTICAL);
        top.setPadding(dp(14), dp(12), dp(14), dp(8));

        top.addView(tv("ME Promo Tracker", 22, Color.WHITE, true));

        status = tv("Ładuję dane...", 13, Color.rgb(203, 213, 225), false);
        status.setPadding(0, dp(5), 0, dp(6));
        top.addView(status);

        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.HORIZONTAL);
        buttons.addView(button("Odśwież dane", v -> runWorkflow()), new LinearLayout.LayoutParams(0, -2, 1));
        buttons.addView(button("Pobierz", v -> loadData()), new LinearLayout.LayoutParams(0, -2, 1));
        buttons.addView(button("Klucz", v -> keyDialog()), new LinearLayout.LayoutParams(0, -2, 1));
        top.addView(buttons);

        bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        bar.setIndeterminate(true);
        bar.setVisibility(View.GONE);
        top.addView(bar, new LinearLayout.LayoutParams(-1, -2));

        summary = tv("", 15, Color.WHITE, true);
        summary.setPadding(0, dp(8), 0, dp(6));
        top.addView(summary);

        search = new EditText(this);
        search.setHint("Szukaj po nazwie, kodzie LEGO, kodzie rabatowym...");
        search.setSingleLine(true);
        search.setTextColor(Color.WHITE);
        search.setHintTextColor(Color.rgb(148, 163, 184));
        search.setBackground(round(Color.rgb(2, 6, 23), Color.rgb(51, 65, 85), dp(14)));
        search.setPadding(dp(10), dp(8), dp(10), dp(8));
        search.addTextChangedListener(new android.text.TextWatcher() {
            public void beforeTextChanged(CharSequence s, int st, int c, int a) {}
            public void onTextChanged(CharSequence s, int st, int b, int c) { render(); }
            public void afterTextChanged(android.text.Editable e) {}
        });
        top.addView(search);

        ScrollView scroll = new ScrollView(this);
        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        list.setPadding(dp(10), dp(8), dp(10), dp(18));
        scroll.addView(list);

        root.addView(top, new LinearLayout.LayoutParams(-1, -2));
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
    }

    private Button button(String text, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setOnClickListener(l);
        return b;
    }

    private void loadData() {
        bar.setVisibility(View.VISIBLE);
        status.setText("Pobieram najnowszy plik z GitHuba...");
        new Thread(() -> {
            try {
                String json = get(DATA_URL + "&nocache=" + System.currentTimeMillis(), true);
                JSONArray arr = new JSONArray(json);
                ui.post(() -> {
                    data = arr;
                    lastLoadMillis = System.currentTimeMillis();
                    bar.setVisibility(View.GONE);
                    status.setText("Dane pobrane bezpośrednio z repozytorium.");
                    render();
                });
            } catch (Exception e) {
                ui.post(() -> {
                    bar.setVisibility(View.GONE);
                    status.setText("Błąd pobierania: " + e.getMessage());
                    Toast.makeText(this, "Nie udało się pobrać danych", Toast.LENGTH_LONG).show();
                });
            }
        }).start();
    }

    private void render() {
        list.removeAllViews();
        String q = search.getText().toString().toLowerCase(Locale.ROOT).trim();
        int visible = 0, codeCount = 0, changed = 0;
        double maxDrop = 0;
        String last = "-";

        for (int i = 0; i < data.length(); i++) {
            JSONObject p = data.optJSONObject(i);
            if (p == null) continue;
            if (i == 0) last = date(p.optString("checked_at"));
            String text = (p.optString("name") + " " + p.optString("lego_code") + " " + p.optString("promo_code")).toLowerCase(Locale.ROOT);
            if (!q.isEmpty() && !text.contains(q)) continue;
            visible++;
            if (!p.isNull("price_with_code")) codeCount++;
            if (p.optBoolean("price_changed_now", false)) changed++;
            double price = p.optDouble("price_gross", 0);
            double code = p.optDouble("price_with_code", 0);
            if (price > 0 && code > 0) maxDrop = Math.max(maxDrop, price - code);
            list.addView(card(p));
        }

        summary.setText("Produkty: " + data.length() + " | Widoczne: " + visible + " | Z kodem: " + codeCount + " | Zmiany: " + changed + "\nAktualizacja: " + last + " | Max obniżka: " + money(maxDrop));
        if (visible == 0) list.addView(tv("Brak produktów dla filtra.", 16, Color.rgb(203, 213, 225), false));
    }

    private View card(JSONObject p) {
        LinearLayout c = new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        c.setPadding(dp(13), dp(11), dp(13), dp(11));
        c.setBackground(round(Color.rgb(30, 41, 59), Color.rgb(71, 85, 105), dp(16)));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.setMargins(0, 0, 0, dp(10));
        c.setLayoutParams(lp);

        c.addView(tv("LEGO " + dash(p.optString("lego_code")), 15, Color.rgb(250, 204, 21), true));
        c.addView(tv(p.optString("name", "-"), 17, Color.WHITE, true));
        c.addView(tv("Cena: " + money(p.optDouble("price_gross", 0)) + " | Z kodem: " + money(p.optDouble("price_with_code", 0)), 15, Color.rgb(226, 232, 240), true));
        c.addView(tv("Kod: " + dash(p.optString("promo_code")), 14, Color.rgb(125, 211, 252), false));
        c.addView(tv("Dostępność: " + dash(p.optString("availability")), 14, Color.rgb(203, 213, 225), false));
        c.addView(tv("Pierwsze pojawienie: " + date(p.optString("first_seen_at")), 13, Color.rgb(203, 213, 225), false));
        c.addView(tv("Ostatnia zmiana ceny: " + date(p.optString("price_changed_at")), 13, Color.rgb(203, 213, 225), false));
        if (p.optBoolean("is_new_product", false)) c.addView(tv("NOWY PRODUKT", 13, Color.rgb(74, 222, 128), true));
        if (p.optBoolean("price_changed_now", false)) c.addView(tv("ZMIANA CENY", 13, Color.rgb(251, 146, 60), true));

        String url = p.optString("url", "");
        if (!url.isEmpty()) c.setOnClickListener(v -> startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))));
        return c;
    }

    private void runWorkflow() {
        String key = savedKey();
        if (key.isEmpty()) {
            keyDialog();
            Toast.makeText(this, "Najpierw wpisz klucz GitHub albo uruchom workflow ręcznie w GitHubie.", Toast.LENGTH_LONG).show();
            openActions();
            return;
        }
        bar.setVisibility(View.VISIBLE);
        status.setText("Uruchamiam ręczne odświeżenie...");
        new Thread(() -> {
            try {
                HttpURLConnection con = (HttpURLConnection) new URL(RUN_URL).openConnection();
                con.setRequestMethod("POST");
                con.setRequestProperty("Accept", "application/vnd.github+json");
                con.setRequestProperty("Authorization", "Bearer " + key);
                con.setRequestProperty("Content-Type", "application/json");
                con.setDoOutput(true);
                try (OutputStream os = con.getOutputStream()) { os.write("{\"ref\":\"main\"}".getBytes(StandardCharsets.UTF_8)); }
                int code = con.getResponseCode();
                ui.post(() -> {
                    bar.setVisibility(View.GONE);
                    if (code == 204) {
                        status.setText("Workflow uruchomiony. Będę odpytywał repo przez kilka minut.");
                        Toast.makeText(this, "Workflow uruchomiony", Toast.LENGTH_LONG).show();
                        ui.postDelayed(() -> loadData(), 90000);
                        ui.postDelayed(() -> loadData(), 210000);
                        ui.postDelayed(() -> loadData(), 330000);
                    } else {
                        status.setText("GitHub API HTTP " + code + ". Otwieram GitHub Actions.");
                        openActions();
                    }
                });
            } catch (Exception e) {
                ui.post(() -> {
                    bar.setVisibility(View.GONE);
                    status.setText("Błąd: " + e.getMessage() + ". Otwieram GitHub Actions.");
                    openActions();
                });
            }
        }).start();
    }

    private void openActions() {
        openedGithubActions = true;
        startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(ACTIONS_URL)));
    }

    private void keyDialog() {
        EditText input = new EditText(this);
        input.setMinLines(2);
        input.setText(savedKey());
        input.setHint("GitHub token do uruchamiania Actions");
        new AlertDialog.Builder(this)
                .setTitle("Klucz GitHub")
                .setMessage("Klucz zapisuje się tylko lokalnie w telefonie.")
                .setView(input)
                .setPositiveButton("Zapisz", (d, w) -> getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY, input.getText().toString().trim()).apply())
                .setNegativeButton("Anuluj", null)
                .show();
    }

    private String savedKey() { return getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY, "").trim(); }

    private String get(String address, boolean rawGithubApi) throws Exception {
        HttpURLConnection con = (HttpURLConnection) new URL(address).openConnection();
        con.setRequestMethod("GET");
        con.setRequestProperty("Cache-Control", "no-cache, no-store, max-age=0");
        con.setRequestProperty("Pragma", "no-cache");
        con.setUseCaches(false);
        if (rawGithubApi) {
            con.setRequestProperty("Accept", "application/vnd.github.raw");
            con.setRequestProperty("X-GitHub-Api-Version", "2022-11-28");
            String key = savedKey();
            if (!key.isEmpty()) con.setRequestProperty("Authorization", "Bearer " + key);
        }
        int code = con.getResponseCode();
        InputStream is = code >= 400 ? con.getErrorStream() : con.getInputStream();
        BufferedReader br = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8));
        StringBuilder sb = new StringBuilder();
        String line;
        while ((line = br.readLine()) != null) sb.append(line);
        br.close();
        if (code >= 400) throw new RuntimeException("HTTP " + code);
        return sb.toString();
    }

    private TextView tv(String s, int size, int color, boolean bold) {
        TextView v = new TextView(this);
        v.setText(s);
        v.setTextSize(size);
        v.setTextColor(color);
        if (bold) v.setTypeface(Typeface.DEFAULT_BOLD);
        v.setPadding(0, dp(2), 0, dp(3));
        return v;
    }

    private String money(double v) { return v <= 0 ? "-" : pln.format(v); }
    private String dash(String s) { return (s == null || s.length() == 0 || "null".equalsIgnoreCase(s)) ? "-" : s; }

    private String date(String iso) {
        if (iso == null || iso.length() == 0 || "null".equalsIgnoreCase(iso)) return "-";
        try {
            SimpleDateFormat in = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US);
            in.setTimeZone(TimeZone.getTimeZone("UTC"));
            Date d = in.parse(iso);
            SimpleDateFormat out = new SimpleDateFormat("dd.MM.yyyy HH:mm", new Locale("pl", "PL"));
            out.setTimeZone(TimeZone.getTimeZone("Europe/Warsaw"));
            return out.format(d);
        } catch (Exception e) { return iso; }
    }

    private GradientDrawable round(int fill, int stroke, int radius) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(fill);
        g.setCornerRadius(radius);
        g.setStroke(dp(1), stroke);
        return g;
    }

    private int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }
}
