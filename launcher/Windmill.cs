// Windmill.exe - the desktop app launcher. Built on the user's PC from this file by `windmill app --install`
// (with the C# compiler that ships with Windows), and rebuilt automatically by updates when this file changes.
// It starts Windmill's local server (the same Python code as the command line), opens the app window and
// stays in the system tray (hidden icons) so Windmill keeps working in the background after the window closes.
// C# 5 only (that's what the Windows-bundled compiler supports): no string interpolation, no "?.".
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

static class WindmillApp
{
    static string Dir { get { return Path.GetDirectoryName(Application.ExecutablePath); } }

    static string Home, Kit, Base, Token;
    static Mutex trayMutex;
    static bool swapFailed;

    [STAThread]
    static int Main(string[] args)
    {
        try
        {
            if (SwapInNewVersion(args)) return 0;
            Kit = FindKit();
            if (Kit == null) { Fail("Windmill's files weren't found next to Windmill.exe. Reinstall Windmill."); return 2; }
            if (args.Length > 0 && args[0] == "--selftest") return SelfTest(Kit);
            bool trayOnly = Array.IndexOf(args, "--tray") >= 0;  // "Start with Windows": no window, just the tray icon

            Home = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".windmill");
            bool firstTray;
            trayMutex = new Mutex(true, @"Local\WindmillTray", out firstTray);
            if (!EnsureEngine(true)) return 4;
            if (!trayOnly) OpenWindow(Base + "?token=" + Token, Home);
            if (!firstTray) return 0;  // another Windmill.exe already shows the tray icon
            RunTray();
            GC.KeepAlive(trayMutex);
            return 0;
        }
        catch (Exception e)
        {
            Fail("Windmill couldn't start:\n" + e.Message);
            return 1;
        }
    }

    // The engine is the local Windmill server (Python). Reuse a running one, or start it.
    static bool EnsureEngine(bool showErrors)
    {
        if (ReadRunning(Home)) return true;
        string python = FindPython();
        if (python == null)
        {
            if (showErrors) Fail("Python wasn't found. Run WindmillSetup.exe (or install Python 3 from python.org) and try again.");
            return false;
        }
        int port = FreePort();
        string token = Guid.NewGuid().ToString("N");
        var psi = new ProcessStartInfo(python, Quote(Path.Combine(Kit, "windmill.py")) + " app --serve --port " + port + " --token " + token);
        psi.WorkingDirectory = Kit;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        Process.Start(psi);
        Base = "http://127.0.0.1:" + port + "/";
        Token = token;
        if (WaitHealthy(Base + "health", 45)) return true;
        if (showErrors)
            Fail("Windmill's engine didn't start.\n\nDetails are in:\n" + Path.Combine(Home, @"logs\app.log") +
                 "\n\nOr open a terminal and run:  windmill app");
        return false;
    }

    // ------------------------------------------------------------ tray icon
    static NotifyIcon tray;
    static ToolStripMenuItem statusItem;
    static int since = -1, misses = 0;
    const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";

    static void RunTray()
    {
        tray = new NotifyIcon();
        try { tray.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch (Exception) { tray.Icon = SystemIcons.Application; }
        tray.Text = "Windmill";
        var menu = new ContextMenuStrip();
        statusItem = new ToolStripMenuItem("Windmill is running");
        statusItem.Enabled = false;
        menu.Items.Add(statusItem);
        menu.Items.Add(new ToolStripSeparator());
        var open = new ToolStripMenuItem("Open Windmill", null, delegate { Open(); });
        open.Font = new Font(open.Font, FontStyle.Bold);
        menu.Items.Add(open);
        var startup = new ToolStripMenuItem("Start with Windows (in the background)");
        startup.Checked = StartsWithWindows();
        startup.Click += delegate { SetStartsWithWindows(!startup.Checked); startup.Checked = StartsWithWindows(); };
        menu.Items.Add(startup);
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(new ToolStripMenuItem("Quit Windmill", null, delegate { Quit(); }));
        tray.ContextMenuStrip = menu;
        tray.MouseClick += delegate(object o, MouseEventArgs e) { if (e.Button == MouseButtons.Left) Open(); };
        tray.BalloonTipClicked += delegate { Open(); };
        tray.Visible = true;

        var timer = new System.Windows.Forms.Timer();
        timer.Interval = 15000;
        timer.Tick += delegate { Poll(); };
        timer.Start();
        Poll();
        Application.Run();
        tray.Visible = false;
    }

    static void Open()
    {
        if (!EnsureEngine(true)) return;
        misses = 0;
        OpenWindow(Base + "?token=" + Token, Home);
    }

    // Every 15 s: what's running, anything finished or waiting for an answer? (This also keeps the engine alive.)
    static void Poll()
    {
        // An update rebuilt Windmill.exe (as Windmill.new.exe): restart the tray on the new version.
        if (!swapFailed && File.Exists(Path.Combine(Dir, "Windmill.new.exe"))) { Relaunch(); return; }
        string json = Http("GET", "api/tray?since=" + Math.Max(since, 0));
        if (json == null)
        {
            if (++misses >= 3 && !ReadRunning(Home)) { statusItem.Text = "Stopped - click Open to start again"; tray.Text = "Windmill (stopped)"; }
            return;
        }
        misses = 0;
        int running = Num(json, "running"), waiting = Num(json, "waiting"), next = Num(json, "next");
        string status = waiting > 0 ? "Waiting for your answer" : running > 0 ? (running + " task" + (running == 1 ? "" : "s") + " running") : "Ready";
        statusItem.Text = "Windmill - " + status;
        tray.Text = ("Windmill - " + status).Length > 63 ? "Windmill" : "Windmill - " + status;
        if (since >= 0)
        {
            Match notes = Regex.Match(json, "\"notes\":\\s*\\[(.*?)\\]", RegexOptions.Singleline);
            string text = "";
            foreach (Match m in Regex.Matches(notes.Groups[1].Value, "\"((?:[^\"\\\\]|\\\\.)*)\""))
                text += Regex.Unescape(m.Groups[1].Value) + "\n";
            if (text.Length > 0) tray.ShowBalloonTip(8000, "Windmill", text.Trim(), ToolTipIcon.Info);
        }
        since = next;
    }

    static int Num(string json, string key)
    {
        int n;
        return int.TryParse(Regex.Match(json, "\"" + key + "\":\\s*(\\d+)").Groups[1].Value, out n) ? n : 0;
    }

    static string Http(string method, string path)
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create(Base + path);
            req.Method = method;
            req.Timeout = 3000;
            req.Proxy = null;
            req.Headers["X-Windmill-Token"] = Token;
            if (method == "POST")
            {
                req.ContentType = "application/json";
                byte[] body = System.Text.Encoding.UTF8.GetBytes("{}");
                req.ContentLength = body.Length;
                using (var s = req.GetRequestStream()) s.Write(body, 0, body.Length);
            }
            using (var res = (HttpWebResponse)req.GetResponse())
            using (var r = new StreamReader(res.GetResponseStream()))
                return r.ReadToEnd();
        }
        catch (Exception) { return null; }
    }

    static void Relaunch()
    {
        tray.Visible = false;
        try { trayMutex.ReleaseMutex(); } catch (Exception) { }
        Process.Start(Application.ExecutablePath, "--tray");  // swaps in Windmill.new.exe, then shows the tray again
        Application.Exit();
    }

    static void Quit()
    {
        Http("POST", "api/shutdown");
        tray.Visible = false;
        Application.Exit();
    }

    static bool StartsWithWindows()
    {
        using (var k = Registry.CurrentUser.OpenSubKey(RunKey))
            return k != null && k.GetValue("Windmill") != null;
    }

    static void SetStartsWithWindows(bool on)
    {
        using (var k = Registry.CurrentUser.CreateSubKey(RunKey))
        {
            if (on) k.SetValue("Windmill", Quote(Application.ExecutablePath) + " --tray");
            else if (k.GetValue("Windmill") != null) k.DeleteValue("Windmill");
        }
    }

    // An update can't overwrite a running .exe, so it builds Windmill.new.exe; we swap it in here.
    static bool SwapInNewVersion(string[] args)
    {
        string me = Application.ExecutablePath;
        string fresh = Path.Combine(Dir, "Windmill.new.exe");
        string old = Path.Combine(Dir, "Windmill.old.exe");
        if (!File.Exists(fresh)) { try { if (File.Exists(old)) File.Delete(old); } catch (Exception) { } return false; }
        for (int i = 0; i < 20; i++)  // the previous copy may still be closing (tray relaunch) - give it a few seconds
        {
            try
            {
                if (File.Exists(old)) File.Delete(old);
                File.Move(me, old);
                File.Move(fresh, me);
                Process.Start(me, string.Join(" ", args));
                return true;
            }
            catch (Exception) { Thread.Sleep(250); }
        }
        swapFailed = true;
        return false;
    }

    static string FindKit()
    {
        foreach (string d in new[] { Dir, Path.GetDirectoryName(Dir) })
            if (d != null && File.Exists(Path.Combine(d, "windmill.py"))) return d;
        return null;
    }

    static string FindPython()
    {
        string saved = Path.Combine(Dir, "python.txt");
        if (File.Exists(saved))
        {
            string p = File.ReadAllText(saved).Trim();
            if (File.Exists(p)) return p;
        }
        foreach (string name in new[] { "pythonw.exe", "pyw.exe", "python.exe" })
        {
            string path = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (string d in path.Split(';'))
            {
                try { string f = Path.Combine(d.Trim(), name); if (File.Exists(f) && !f.Contains("WindowsApps")) return f; }
                catch (Exception) { }
            }
        }
        return null;
    }

    static bool ReadRunning(string home)
    {
        try
        {
            string json = File.ReadAllText(Path.Combine(home, "app.json"));
            string port = Regex.Match(json, "\"port\":\\s*(\\d+)").Groups[1].Value;
            string token = Regex.Match(json, "\"token\":\\s*\"([^\"]+)\"").Groups[1].Value;
            if (port.Length > 0 && WaitHealthy("http://127.0.0.1:" + port + "/health", 1))
            {
                Base = "http://127.0.0.1:" + port + "/";
                Token = token;
                return true;
            }
        }
        catch (Exception) { }
        return false;
    }

    static void OpenWindow(string url, string home)
    {
        string[] browsers = {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), @"Microsoft\Edge\Application\msedge.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"Microsoft\Edge\Application\msedge.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"Google\Chrome\Application\chrome.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Google\Chrome\Application\chrome.exe"),
        };
        foreach (string b in browsers)
        {
            if (!File.Exists(b)) continue;
            string profile = Path.Combine(home, "app-window");
            Process.Start(b, "--app=" + Quote(url) + " --user-data-dir=" + Quote(profile) +
                " --window-size=1320,880 --no-first-run --no-default-browser-check --disable-features=Translate");
            return;
        }
        Process.Start(url);  // no Edge/Chrome: the default browser
    }

    static bool WaitHealthy(string url, int seconds)
    {
        DateTime end = DateTime.Now.AddSeconds(seconds);
        do
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Timeout = 1000;
                req.Proxy = null;
                using (var res = (HttpWebResponse)req.GetResponse())
                    if (res.StatusCode == HttpStatusCode.OK) return true;
            }
            catch (Exception) { }
            Thread.Sleep(300);
        } while (DateTime.Now < end);
        return false;
    }

    static int FreePort()
    {
        var l = new TcpListener(IPAddress.Loopback, 0);
        l.Start();
        int p = ((IPEndPoint)l.LocalEndpoint).Port;
        l.Stop();
        return p;
    }

    static int SelfTest(string kit)
    {
        string py = FindPython();
        Console.WriteLine("kit=" + kit + " python=" + (py ?? "MISSING"));
        return py == null ? 3 : 0;
    }

    static string Quote(string s) { return "\"" + s + "\""; }

    static void Fail(string msg) { MessageBox.Show(msg, "Windmill", MessageBoxButtons.OK, MessageBoxIcon.Warning); }
}
