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
                String base = prefs.getString("base", "");
                if (u.getScheme() != null && u.getScheme().startsWith("http") && !base.isEmpty()
                        && !u.toString().startsWith(base)) {
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

    private void open() {
        String base = prefs.getString("base", "");
        String token = prefs.getString("token", "");
        if (base.isEmpty() || token.isEmpty()) {
            web.loadUrl("file:///android_asset/pair.html");
        } else {
            web.loadUrl(base + "/?dev=" + Uri.encode(token));
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
            return prefs.getString("base", "");
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
