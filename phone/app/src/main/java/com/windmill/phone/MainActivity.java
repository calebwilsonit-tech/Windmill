package com.windmill.phone;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.Set;

/**
 * The Windmill phone app: a full-screen window onto Windmill running on your computer.
 * The AI never runs on the phone - this shows the computer's Windmill app (chats, builds, thinking) and sends
 * what you type back to it.
 *
 * First run: find the computer on the Wi-Fi (or type its address, e.g. a Tailscale one) and pair with the
 * 6-digit code it shows. The computer then gives this phone its own key, which is kept here.
 */
public class MainActivity extends Activity {
    private static final int DISCOVERY_PORT = 8766;
    private WebView web;
    private SharedPreferences prefs;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences("windmill", MODE_PRIVATE);
        web = new WebView(this);
        setContentView(web);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        s.setMediaPlaybackRequiresUserGesture(true);
        CookieManager.getInstance().setAcceptCookie(true);
        web.addJavascriptInterface(new Bridge(), "WindmillApp");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                String base = prefs.getString("base", ""), remote = prefs.getString("remote", "");
                String url = u.toString();
                if (u.getScheme() != null && u.getScheme().startsWith("http") && !base.isEmpty()
                        && !url.startsWith(base) && (remote.isEmpty() || !url.startsWith(remote))) {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));  // other websites open in the browser
                    return true;
                }
                return false;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame() && req.getUrl().toString().startsWith("http")) {
                    view.loadUrl("file:///android_asset/offline.html");
                }
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                CookieManager.getInstance().flush();  // keep the pairing cookie across restarts
            }
        });
        if (state != null) {
            web.restoreState(state);
        } else {
            open();
        }
    }

    // ------------------------------------------------------------ updating this app from the computer
    // Everything you see comes from the computer, so it's always current. Only this small app (pairing, discovery,
    // the offline screen) is installed on the phone; when the computer has a newer one, offer it once per start.
    private boolean updateChecked = false;

    @Override
    protected void onResume() {
        super.onResume();
        String base = current.isEmpty() ? prefs.getString("base", "") : current;
        if (updateChecked || base.isEmpty()) return;
        updateChecked = true;
        new Thread(() -> {
            try {
                java.net.HttpURLConnection c = (java.net.HttpURLConnection) new java.net.URL(base + "/api/phone/hello").openConnection();
                c.setConnectTimeout(4000);
                c.setReadTimeout(4000);
                String body = new String(readAll(c.getInputStream()), StandardCharsets.UTF_8);
                int latest = new JSONObject(body).optInt("app", 0);
                long mine = getPackageManager().getPackageInfo(getPackageName(), 0).getLongVersionCode();
                if (latest > mine && latest != prefs.getInt("skipped", 0)) {
                    runOnUiThread(() -> new android.app.AlertDialog.Builder(this)
                            .setTitle("Update the Windmill app?")
                            .setMessage("Your computer has a newer version of this app. It installs over this one - you stay paired.")
                            .setPositiveButton("Update", (d, w) -> new Thread(() -> install(base)).start())
                            .setNegativeButton("Not now", (d, w) -> prefs.edit().putInt("skipped", latest).apply())
                            .show());
                }
            } catch (Exception ignored) { }
        }).start();
    }

    private void install(String base) {
        try {
            android.content.pm.PackageInstaller pi = getPackageManager().getPackageInstaller();
            int id = pi.createSession(new android.content.pm.PackageInstaller.SessionParams(
                    android.content.pm.PackageInstaller.SessionParams.MODE_FULL_INSTALL));
            try (android.content.pm.PackageInstaller.Session s = pi.openSession(id)) {
                java.net.HttpURLConnection c = (java.net.HttpURLConnection) new java.net.URL(base + "/phone.apk").openConnection();
                c.setConnectTimeout(8000);
                try (java.io.InputStream in = c.getInputStream(); java.io.OutputStream out = s.openWrite("windmill.apk", 0, -1)) {
                    byte[] buf = new byte[65536];
                    int n;
                    while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                    s.fsync(out);
                }
                Intent done = new Intent(this, MainActivity.class).setAction("com.windmill.phone.INSTALL");
                android.app.PendingIntent p = android.app.PendingIntent.getActivity(this, 1, done,
                        android.app.PendingIntent.FLAG_UPDATE_CURRENT | android.app.PendingIntent.FLAG_MUTABLE);
                s.commit(p.getIntentSender());
            }
        } catch (Exception e) {
            runOnUiThread(() -> android.widget.Toast.makeText(this, "Couldn't download the update - is the computer still reachable?",
                    android.widget.Toast.LENGTH_LONG).show());
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (!"com.windmill.phone.INSTALL".equals(intent.getAction())) return;
        int status = intent.getIntExtra(android.content.pm.PackageInstaller.EXTRA_STATUS, -999);
        if (status == android.content.pm.PackageInstaller.STATUS_PENDING_USER_ACTION) {
            Intent confirm = intent.getParcelableExtra(Intent.EXTRA_INTENT);  // Android's own "Update this app?" screen
            if (confirm != null) startActivity(confirm);
        } else if (status != android.content.pm.PackageInstaller.STATUS_SUCCESS) {
            android.widget.Toast.makeText(this, "The update didn't install: " +
                    intent.getStringExtra(android.content.pm.PackageInstaller.EXTRA_STATUS_MESSAGE), android.widget.Toast.LENGTH_LONG).show();
        }
    }

    private static byte[] readAll(java.io.InputStream in) throws java.io.IOException {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        return out.toByteArray();
    }

    /** Open Windmill by whichever route works: the home Wi-Fi address first (fastest), else the anywhere-address. */
    private void open() {
        String base = prefs.getString("base", "");
        String token = prefs.getString("token", "");
        if (base.isEmpty() || token.isEmpty()) {
            web.loadUrl("file:///android_asset/pair.html");
            return;
        }
        new Thread(() -> {
            String remote = prefs.getString("remote", "");
            String chosen = null;
            JSONObject hello = hello(base, 1500);
            if (hello != null) {
                chosen = base;
                String r = hello.optString("remote", "");  // keep the anywhere-address up to date while at home
                if (!r.isEmpty() && !r.equals(remote)) prefs.edit().putString("remote", r).apply();
            } else if (!remote.isEmpty() && hello(remote, 8000) != null) {
                chosen = remote;
            }
            final String go = chosen;
            runOnUiThread(() -> {
                current = go == null ? base : go;
                if (go == null) web.loadUrl("file:///android_asset/offline.html");
                else web.loadUrl(go + "/?dev=" + Uri.encode(token));
            });
        }).start();
    }

    private String current = "";

    private static JSONObject hello(String base, int timeoutMs) {
        try {
            java.net.HttpURLConnection c = (java.net.HttpURLConnection) new java.net.URL(base + "/api/phone/hello").openConnection();
            c.setConnectTimeout(timeoutMs);
            c.setReadTimeout(timeoutMs);
            JSONObject j = new JSONObject(new String(readAll(c.getInputStream()), StandardCharsets.UTF_8));
            return j.optBoolean("windmill") ? j : null;
        } catch (Exception e) {
            return null;
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    /** What the pairing / offline pages can ask the app to do. */
    private class Bridge {
        @JavascriptInterface
        public String discover() {
            JSONArray found = new JSONArray();
            Set<String> seen = new HashSet<>();
            WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            WifiManager.MulticastLock lock = wifi != null ? wifi.createMulticastLock("windmill") : null;
            if (lock != null) lock.acquire();
            try (DatagramSocket sock = new DatagramSocket()) {
                sock.setBroadcast(true);
                sock.setSoTimeout(1500);
                byte[] ask = "WINDMILL?".getBytes(StandardCharsets.US_ASCII);
                for (String target : new String[]{"255.255.255.255", subnetBroadcast(wifi)}) {
                    if (target == null) continue;
                    try {
                        sock.send(new DatagramPacket(ask, ask.length, InetAddress.getByName(target), DISCOVERY_PORT));
                    } catch (Exception ignored) { }
                }
                long end = System.currentTimeMillis() + 1800;
                byte[] buf = new byte[512];
                while (System.currentTimeMillis() < end) {
                    DatagramPacket p = new DatagramPacket(buf, buf.length);
                    try {
                        sock.receive(p);
                    } catch (SocketTimeoutException e) {
                        break;
                    }
                    String ip = p.getAddress().getHostAddress();
                    if (seen.contains(ip)) continue;
                    seen.add(ip);
                    JSONObject reply = new JSONObject(new String(p.getData(), 0, p.getLength(), StandardCharsets.UTF_8));
                    JSONObject c = new JSONObject();
                    c.put("name", reply.optString("name", ip));
                    c.put("base", "http://" + ip + ":" + reply.optInt("port", 8765));
                    found.put(c);
                }
            } catch (Exception ignored) {
            } finally {
                if (lock != null && lock.isHeld()) lock.release();
            }
            return found.toString();
        }

        @JavascriptInterface
        public void save(String base, String token) {
            prefs.edit().putString("base", base).putString("token", token).apply();
        }

        @JavascriptInterface
        public void setRemote(String remote) {
            prefs.edit().putString("remote", remote == null ? "" : remote).apply();
        }

        @JavascriptInterface
        public void forget() {
            prefs.edit().clear().apply();
            CookieManager.getInstance().removeAllCookies(null);
            runOnUiThread(() -> web.loadUrl("file:///android_asset/pair.html"));
        }

        @JavascriptInterface
        public void retry() {
            runOnUiThread(MainActivity.this::open);
        }

        @JavascriptInterface
        public String computer() {
            return current.isEmpty() ? prefs.getString("base", "") : current;
        }

        @JavascriptInterface
        public String remote() {
            return prefs.getString("remote", "");
        }

        @JavascriptInterface
        public String deviceName() {
            String m = Build.MODEL == null ? "Phone" : Build.MODEL;
            return Build.MANUFACTURER != null && !m.toLowerCase().startsWith(Build.MANUFACTURER.toLowerCase())
                    ? Build.MANUFACTURER + " " + m : m;
        }
    }

    @SuppressWarnings("deprecation")
    private static String subnetBroadcast(WifiManager wifi) {
        try {
            if (wifi == null || wifi.getDhcpInfo() == null) return null;
            int ip = wifi.getDhcpInfo().ipAddress, mask = wifi.getDhcpInfo().netmask;
            if (ip == 0 || mask == 0) return null;
            int b = (ip & mask) | ~mask;
            return (b & 0xff) + "." + ((b >> 8) & 0xff) + "." + ((b >> 16) & 0xff) + "." + ((b >> 24) & 0xff);
        } catch (Exception e) {
            return null;
        }
    }
}
