// Windmill.exe - the desktop app launcher. Built on the user's PC from this file by `windmill app --install`
// (with the C# compiler that ships with Windows), and rebuilt automatically by updates when this file changes.
// It starts Windmill's local server (the same Python code as the command line) and opens the app window.
// C# 5 only (that's what the Windows-bundled compiler supports): no string interpolation, no "?.".
using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

static class WindmillApp
{
    static string Dir { get { return Path.GetDirectoryName(Application.ExecutablePath); } }

    [STAThread]
    static int Main(string[] args)
    {
        try
        {
            if (SwapInNewVersion(args)) return 0;
            string kit = FindKit();
            if (kit == null) { Fail("Windmill's files weren't found next to Windmill.exe. Reinstall Windmill."); return 2; }
            if (args.Length > 0 && args[0] == "--selftest") return SelfTest(kit);

            string home = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".windmill");
            string existing = ReadRunning(home);
            if (existing != null) { OpenWindow(existing, home); return 0; }

            string python = FindPython();
            if (python == null) { Fail("Python wasn't found. Run WindmillSetup.exe (or install Python 3 from python.org) and try again."); return 3; }

            int port = FreePort();
            string token = Guid.NewGuid().ToString("N");
            var psi = new ProcessStartInfo(python, Quote(Path.Combine(kit, "windmill.py")) + " app --serve --port " + port + " --token " + token);
            psi.WorkingDirectory = kit;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            Process.Start(psi);

            string url = "http://127.0.0.1:" + port + "/";
            if (!WaitHealthy(url + "health", 45))
            {
                Fail("Windmill's engine didn't start. Open a terminal and run:  windmill doctor");
                return 4;
            }
            OpenWindow(url + "?token=" + token, home);
            return 0;
        }
        catch (Exception e)
        {
            Fail("Windmill couldn't start:\n" + e.Message);
            return 1;
        }
    }

    // An update can't overwrite a running .exe, so it builds Windmill.new.exe; we swap it in here.
    static bool SwapInNewVersion(string[] args)
    {
        string me = Application.ExecutablePath;
        string fresh = Path.Combine(Dir, "Windmill.new.exe");
        string old = Path.Combine(Dir, "Windmill.old.exe");
        try { if (File.Exists(old)) File.Delete(old); } catch (Exception) { }
        if (!File.Exists(fresh)) return false;
        try
        {
            File.Move(me, old);
            File.Move(fresh, me);
            Process.Start(me, string.Join(" ", args));
            return true;
        }
        catch (Exception) { return false; }
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

    static string ReadRunning(string home)
    {
        try
        {
            string json = File.ReadAllText(Path.Combine(home, "app.json"));
            string port = Regex.Match(json, "\"port\":\\s*(\\d+)").Groups[1].Value;
            string token = Regex.Match(json, "\"token\":\\s*\"([^\"]+)\"").Groups[1].Value;
            if (port.Length > 0 && WaitHealthy("http://127.0.0.1:" + port + "/health", 1))
                return "http://127.0.0.1:" + port + "/?token=" + token;
        }
        catch (Exception) { }
        return null;
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
