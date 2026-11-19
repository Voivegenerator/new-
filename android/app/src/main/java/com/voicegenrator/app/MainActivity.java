package com.voicegenrator.app;

import android.Manifest;
import android.app.Activity;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.ContentValues;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;

public class MainActivity extends Activity {

    private static final String HOST = "aivoicestudio.onrender.com";
    private static final String HOME = "https://" + HOST + "/";
    private static final int BG = 0xFF090B19;
    private static final int REQ_STORAGE = 7;

    private WebView web;
    private ProgressBar bar;
    private View loading;
    private View offline;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(BG);

        web = new WebView(this);
        web.setBackgroundColor(BG);
        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        bar.setMax(100);
        root.addView(bar, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(4), Gravity.TOP));

        loading = buildMessage("◉ VoiceGenrator",
                "ऐप खुल रहा है…\nपहली बार खुलने में 1 मिनट तक लग सकता है।", null);
        root.addView(loading);

        offline = buildMessage("इंटरनेट कनेक्शन नहीं मिला",
                "कृपया इंटरनेट चालू करके दोबारा कोशिश करें।", "दोबारा कोशिश करें");
        offline.setVisibility(View.GONE);
        root.addView(offline);

        setContentView(root);
        setupWebView();

        if (savedInstanceState != null) {
            web.restoreState(savedInstanceState);
        } else {
            web.loadUrl(startUrl(getIntent()));
        }
    }

    private void setupWebView() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setUserAgentString(s.getUserAgentString() + " VoiceGenratorApp/1.0");

        web.addJavascriptInterface(new Bridge(), "AndroidBridge");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (HOST.equals(uri.getHost())) {
                    return false;
                }
                openExternal(uri);
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                loading.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    loading.setVisibility(View.GONE);
                    offline.setVisibility(View.VISIBLE);
                }
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int progress) {
                bar.setProgress(progress);
                bar.setVisibility(progress >= 100 ? View.GONE : View.VISIBLE);
            }
        });

        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, length) -> {
            if (url.startsWith("blob:")) {
                String name = "VoiceGenrator-" + System.currentTimeMillis() + ".mp3";
                String js = "(async()=>{try{const r=await fetch(" + JSONObject.quote(url) + ");"
                        + "const b=await r.blob();const fr=new FileReader();"
                        + "fr.onloadend=()=>AndroidBridge.saveBase64(fr.result.split(',')[1],"
                        + JSONObject.quote(name) + ",b.type||'audio/mpeg');fr.readAsDataURL(b);}"
                        + "catch(e){AndroidBridge.toast('डाउनलोड नहीं हो सका')}})()";
                web.evaluateJavascript(js, null);
            } else if (url.startsWith("http")) {
                downloadWithManager(url, userAgent, contentDisposition, mimeType);
            }
        });
    }

    private String startUrl(Intent intent) {
        if (intent != null && intent.getData() != null && HOST.equals(intent.getData().getHost())) {
            return intent.getData().toString();
        }
        return HOME;
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (intent != null && intent.getData() != null) {
            web.loadUrl(startUrl(intent));
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    private void openExternal(Uri uri) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (ActivityNotFoundException e) {
            toast("यह लिंक खोलने के लिए कोई ऐप नहीं मिला");
        }
    }

    private boolean needsStoragePermission() {
        return Build.VERSION.SDK_INT < 29
                && checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED;
    }

    private void downloadWithManager(String url, String userAgent, String contentDisposition, String mimeType) {
        if (needsStoragePermission()) {
            requestPermissions(new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE);
            toast("अनुमति देने के बाद दोबारा डाउनलोड दबाएँ");
            return;
        }
        try {
            String name = URLUtil.guessFileName(url, contentDisposition, mimeType);
            if (!name.contains(".")) {
                name = name + ".mp3";
            }
            DownloadManager.Request req = new DownloadManager.Request(Uri.parse(url));
            req.setMimeType(mimeType);
            req.addRequestHeader("User-Agent", userAgent);
            req.setTitle(name);
            req.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            req.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "VoiceGenrator/" + name);
            DownloadManager dm = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
            dm.enqueue(req);
            toast("डाउनलोड शुरू हुआ: Downloads/VoiceGenrator");
        } catch (Exception e) {
            toast("डाउनलोड नहीं हो सका");
        }
    }

    private class Bridge {
        @JavascriptInterface
        public void saveBase64(String base64, String name, String mime) {
            try {
                byte[] data = Base64.decode(base64, Base64.DEFAULT);
                if (Build.VERSION.SDK_INT >= 29) {
                    ContentValues cv = new ContentValues();
                    cv.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
                    cv.put(MediaStore.MediaColumns.MIME_TYPE, mime);
                    cv.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/VoiceGenrator");
                    Uri uri = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv);
                    if (uri == null) {
                        throw new IllegalStateException("insert failed");
                    }
                    try (OutputStream out = getContentResolver().openOutputStream(uri)) {
                        out.write(data);
                    }
                } else {
                    if (needsStoragePermission()) {
                        runOnUiThread(() -> requestPermissions(
                                new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE));
                        toast("अनुमति देने के बाद दोबारा डाउनलोड दबाएँ");
                        return;
                    }
                    File dir = new File(Environment.getExternalStoragePublicDirectory(
                            Environment.DIRECTORY_DOWNLOADS), "VoiceGenrator");
                    if (!dir.exists() && !dir.mkdirs()) {
                        throw new IllegalStateException("mkdir failed");
                    }
                    try (FileOutputStream out = new FileOutputStream(new File(dir, name))) {
                        out.write(data);
                    }
                }
                toast("MP3 सेव हो गई: Downloads/VoiceGenrator");
            } catch (Exception e) {
                toast("MP3 सेव नहीं हो सकी");
            }
        }

        @JavascriptInterface
        public void toast(String message) {
            MainActivity.this.toast(message);
        }
    }

    private void toast(String message) {
        runOnUiThread(() -> Toast.makeText(this, message, Toast.LENGTH_LONG).show());
    }

    private View buildMessage(String title, String body, String buttonText) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setBackgroundColor(BG);
        box.setPadding(dp(32), dp(32), dp(32), dp(32));
        box.setClickable(true);

        TextView t = new TextView(this);
        t.setText(title);
        t.setTextColor(Color.WHITE);
        t.setTextSize(24);
        t.setGravity(Gravity.CENTER);
        box.addView(t);

        TextView b = new TextView(this);
        b.setText(body);
        b.setTextColor(0xFFB9B8D0);
        b.setTextSize(15);
        b.setGravity(Gravity.CENTER);
        b.setPadding(0, dp(14), 0, dp(18));
        box.addView(b);

        if (buttonText != null) {
            Button btn = new Button(this);
            btn.setText(buttonText);
            btn.setOnClickListener(v -> {
                offline.setVisibility(View.GONE);
                loading.setVisibility(View.VISIBLE);
                if (web.getUrl() == null) {
                    web.loadUrl(HOME);
                } else {
                    web.reload();
                }
            });
            box.addView(btn);
        }
        return box;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
