// WindmillSetup.exe - one-click installer. Installs Git and Python if missing (winget), downloads Windmill from
// GitHub into %LOCALAPPDATA%\Programs\Windmill, builds the app + shortcuts, and opens it. Re-running it repairs.
// Updates afterwards are automatic (the app asks on start). C# 5 only.
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Threading;
using System.Windows.Forms;

static class WindmillSetup
{
    const string Repo = "https://github.com/calebwilsonit-tech/Windmill.git";
    static Label status;
    static TextBox log;
    static Form form;

    [STAThread]
    static void Main()
    {
        Application.EnableVisualStyles();
        form = new Form { Text = "Install Windmill", Width = 620, Height = 420, StartPosition = FormStartPosition.CenterScreen,
                          FormBorderStyle = FormBorderStyle.FixedDialog, MaximizeBox = false };
        status = new Label { Text = "Getting ready...", Dock = DockStyle.Top, Height = 40, Font = new Font("Segoe UI", 11f),
                             Padding = new Padding(12, 10, 12, 0) };
        log = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, Dock = DockStyle.Fill,
                            Font = new Font("Consolas", 9f) };
        form.Controls.Add(log);
        form.Controls.Add(status);
        form.Shown += delegate { new Thread(Install) { IsBackground = true }.Start(); };
        Application.Run(form);
    }

    static void Say(string s)
    {
        form.BeginInvoke((Action)delegate { status.Text = s; log.AppendText(s + Environment.NewLine); });
    }

    static void Install()
    {
        try
        {
            string git = Find("git.exe", @"Git\cmd\git.exe");
            if (git == null) { Say("Installing Git..."); Winget("Git.Git"); git = Find("git.exe", @"Git\cmd\git.exe"); }
            string py = FindPython();
            if (py == null) { Say("Installing Python..."); Winget("Python.Python.3.12"); py = FindPython(); }
            if (git == null || py == null) { Done("Git or Python couldn't be installed automatically. Install them, then run this again.", false); return; }

            string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Windmill");
            if (Directory.Exists(Path.Combine(dir, ".git"))) { Say("Updating Windmill..."); Run(git, "-C \"" + dir + "\" pull --ff-only"); }
            else { Say("Downloading Windmill..."); Run(git, "clone " + Repo + " \"" + dir + "\""); }
            if (!File.Exists(Path.Combine(dir, "windmill.py"))) { Done("Downloading Windmill failed - check your internet connection.", false); return; }

            Say("Building the app and shortcuts...");
            Run(py, "\"" + Path.Combine(dir, "windmill.py") + "\" app --install");
            Say("Adding the 'windmill' command for terminals...");
            string bin = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @".windmill\bin");
            Directory.CreateDirectory(bin);
            File.WriteAllText(Path.Combine(bin, "windmill.cmd"),
                "@echo off\r\n\"" + py + "\" \"" + Path.Combine(dir, "windmill.py") + "\" %*\r\n");
            string userPath = Environment.GetEnvironmentVariable("PATH", EnvironmentVariableTarget.User) ?? "";
            if (userPath.IndexOf(bin, StringComparison.OrdinalIgnoreCase) < 0)
                Environment.SetEnvironmentVariable("PATH", userPath.TrimEnd(';') + ";" + bin, EnvironmentVariableTarget.User);
            string exe = Path.Combine(dir, @"bin\Windmill.exe");
            if (!File.Exists(exe)) { Done("The app couldn't be built. Open a terminal and run:  windmill app --install", false); return; }
            Done("Windmill is installed. It will now open - the first time, choose 'Setup & health' to pick an AI model.", true);
            Process.Start(exe);
        }
        catch (Exception e) { Done("Install failed: " + e.Message, false); }
    }

    static void Done(string msg, bool ok)
    {
        Say(msg);
        form.BeginInvoke((Action)delegate {
            var b = new Button { Text = ok ? "Close" : "OK", Dock = DockStyle.Bottom, Height = 36 };
            b.Click += delegate { form.Close(); };
            form.Controls.Add(b);
        });
    }

    static void Run(string exe, string args)
    {
        var psi = new ProcessStartInfo(exe, args) { UseShellExecute = false, CreateNoWindow = true,
                                                     RedirectStandardOutput = true, RedirectStandardError = true };
        var p = Process.Start(psi);
        p.OutputDataReceived += (s, e) => { if (e.Data != null) Say("  " + e.Data); };
        p.ErrorDataReceived += (s, e) => { if (e.Data != null) Say("  " + e.Data); };
        p.BeginOutputReadLine();
        p.BeginErrorReadLine();
        p.WaitForExit();
    }

    static void Winget(string id)
    {
        if (Find("winget.exe", null) == null) return;
        Run("winget", "install -e --id " + id + " --accept-source-agreements --accept-package-agreements --silent");
    }

    static string Find(string exe, string programFilesRel)
    {
        string path = (Environment.GetEnvironmentVariable("PATH", EnvironmentVariableTarget.Machine) ?? "") + ";" +
                      (Environment.GetEnvironmentVariable("PATH", EnvironmentVariableTarget.User) ?? "") + ";" +
                      Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Microsoft\WindowsApps");
        foreach (string d in path.Split(';'))
        {
            try { string f = Path.Combine(d.Trim(), exe); if (d.Trim().Length > 0 && File.Exists(f)) return f; } catch (Exception) { }
        }
        if (programFilesRel != null)
        {
            string f = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), programFilesRel);
            if (File.Exists(f)) return f;
        }
        return null;
    }

    static string FindPython()
    {
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        foreach (string v in new[] { "313", "312", "311", "310", "39" })
        {
            string f = Path.Combine(local, @"Programs\Python\Python" + v + @"\python.exe");
            if (File.Exists(f)) return f;
        }
        string p = Find("python.exe", null);
        return p != null && !p.Contains("WindowsApps") ? p : null;
    }
}
