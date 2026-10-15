// A stand-in agent for the live smoke test (tools/smoke.py setup --fake).
//
// Built twice, as claude.exe and codex.exe, because discovery knows an agent
// by its image name; the name is also the kind it plays. It spends nothing
// and keeps no conversation, so `up`, a named `restart`, `down` and capture
// can be run against it on a busy desktop without touching a real session.
//
// It quits the way agents.py says its kind does: Claude Code on `/exit`,
// either kind on two Ctrl+C within five seconds. Like both real agents it
// reads the console raw, so Ctrl+C arrives as a key: left to the console, it
// is a signal to every process attached, the tab's PowerShell loop included,
// which stops the loop that a restart relies on. It titles its tab the way a
// hand-opened Codex tab is titled ("<spinner> <dir> | <dir>"), which only a
// tab that does not suppress application titles will show.
//
// C# 5, for the compiler every Windows 10/11 machine carries
// (%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe).
using System;
using System.Diagnostics;
using System.IO;
using System.Text;

static class FakeAgent
{
    static string kind;
    static DateTime lastInterrupt = DateTime.MinValue;

    static int Main(string[] args)
    {
        kind = Path.GetFileNameWithoutExtension(
            Process.GetCurrentProcess().MainModule.FileName).ToLowerInvariant();
        string dir = Path.GetFileName(Environment.CurrentDirectory.TrimEnd('\\'));
        Console.WriteLine("[fake {0}] pid {1} in {2}", kind,
                          Process.GetCurrentProcess().Id, Environment.CurrentDirectory);
        Console.WriteLine("[fake {0}] args: {1}", kind, string.Join(" ", args));
        if (Console.IsInputRedirected)
        {
            string piped;
            while ((piped = Console.ReadLine()) != null)
                if (Typed(piped))
                    return 0;
            return 0;
        }
        Console.Title = "⠼ " + dir + " | " + dir;
        Console.TreatControlCAsInput = true;
        StringBuilder line = new StringBuilder();
        while (true)
        {
            ConsoleKeyInfo key = Console.ReadKey(true);
            if (key.Key == ConsoleKey.C && (key.Modifiers & ConsoleModifiers.Control) != 0)
            {
                DateTime now = DateTime.UtcNow;
                if ((now - lastInterrupt).TotalSeconds < 5)
                {
                    Console.WriteLine();
                    Console.WriteLine("[fake {0}] quit", kind);
                    return 0;
                }
                lastInterrupt = now;
                Console.WriteLine();
                Console.WriteLine("[fake {0}] Ctrl+C again to quit", kind);
            }
            else if (key.Key == ConsoleKey.Enter)
            {
                Console.WriteLine();
                if (Typed(line.ToString()))
                    return 0;
                line.Clear();
            }
            else if (key.Key == ConsoleKey.Backspace)
            {
                if (line.Length > 0)
                {
                    line.Length--;
                    Console.Write("\b \b");
                }
            }
            else if (key.KeyChar >= ' ')
            {
                // Not Esc, which teardown sends first to clear an overlay:
                // kept, it made the line "\x1b/exit", and echoed, the terminal
                // read "\x1b/e" as an escape sequence.
                line.Append(key.KeyChar);
                Console.Write(key.KeyChar);
            }
        }
    }

    // A line typed at the prompt; true when it quits.
    static bool Typed(string line)
    {
        if (kind == "claude" && line.Trim() == "/exit")
        {
            Console.WriteLine("[fake claude] /exit");
            return true;
        }
        Console.WriteLine("[fake {0}] > {1}", kind, line);
        return false;
    }
}
