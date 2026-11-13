// Écrans du bureau distant : moniteurs du poste, zone montrée selon l'écran choisi dans le navigateur, annonce de la
// liste au navigateur. Lecture directe de Windows à chaque appel : ni cache ni fenêtre cachée (System.Windows.Forms.Screen
// en garde une), le compagnon devant pouvoir suivre le bureau sécurisé.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Text;

namespace Loco
{
    internal struct DesktopScreen
    {
        public int Number;          // numéro de Windows : 1 pour \\.\DISPLAY1, celui des paramètres d'affichage
        public Rectangle Bounds;    // en pixels, dans les coordonnées du bureau (l'écran principal est en 0,0)
        public bool Primary;
    }

    internal static class DesktopScreens
    {
        internal static List<DesktopScreen> List()
        {
            var found = new List<DesktopScreen>();
            MonitorProc each = (monitor, dc, rect, data) =>
            {
                var info = new MonitorInfo { Size = Marshal.SizeOf(typeof(MonitorInfo)) };
                if (GetMonitorInfo(monitor, ref info))
                    found.Add(new DesktopScreen { Number = Number(info.Device), Primary = (info.Flags & 1) != 0,
                                                  Bounds = Rectangle.FromLTRB(info.Left, info.Top, info.Right, info.Bottom) });
                return true;
            };
            EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, each, IntPtr.Zero);
            GC.KeepAlive(each);
            return Order(found);
        }

        // « \\.\DISPLAY2 » -> 2 ; 0 si le nom ne se termine pas par un numéro.
        internal static int Number(string device)
        {
            int end = (device ?? "").Length, start = end;
            while (start > 0 && device[start - 1] >= '0' && device[start - 1] <= '9') start--;
            int number;
            return end - start >= 1 && end - start <= 4 && Int32.TryParse(device.Substring(start), out number) ? number : 0;
        }

        // Écrans dans l'ordre de leur numéro. Numéros absents ou en double (pilote inhabituel) : renumérotés de gauche à
        // droite puis de haut en bas, pour que chaque écran garde un numéro à lui.
        internal static List<DesktopScreen> Order(List<DesktopScreen> found)
        {
            var screens = new List<DesktopScreen>(found);
            var seen = new HashSet<int>();
            bool plain = true;
            foreach (var s in screens) plain &= s.Number > 0 && seen.Add(s.Number);
            if (plain) screens.Sort((a, b) => a.Number.CompareTo(b.Number));
            else
            {
                screens.Sort((a, b) => a.Bounds.Left != b.Bounds.Left ? a.Bounds.Left.CompareTo(b.Bounds.Left) : a.Bounds.Top.CompareTo(b.Bounds.Top));
                for (int i = 0; i < screens.Count; i++) { var s = screens[i]; s.Number = i + 1; screens[i] = s; }
            }
            return screens;
        }

        // Écran réellement montré : celui demandé s'il existe encore et qu'il y en a plusieurs ; sinon 0, tout le bureau.
        internal static int Valid(IList<DesktopScreen> screens, int selected)
        {
            if (selected <= 0 || screens.Count < 2) return 0;
            foreach (var s in screens) if (s.Number == selected && Usable(s.Bounds)) return selected;
            return 0;
        }

        internal static bool Usable(Rectangle r) { return r.Width > 0 && r.Height > 0 && (long)r.Width * r.Height <= 16777216; }

        // Zone du bureau à capturer : l'écran choisi, ou tout le bureau (all).
        internal static Rectangle Area(IList<DesktopScreen> screens, int selected, Rectangle all)
        {
            if (Valid(screens, selected) != 0) foreach (var s in screens) if (s.Number == selected) return s.Bounds;
            return all;
        }

        // Annonce au navigateur : écran montré (0 = tous) et liste, positions comptées depuis le coin du bureau entier.
        internal static string Describe(IList<DesktopScreen> screens, int selected, Rectangle all)
        {
            var text = new StringBuilder("{\"selected\":" + Valid(screens, selected) + ",\"screens\":[");
            for (int i = 0; i < screens.Count; i++)
            {
                var s = screens[i];
                text.Append(i == 0 ? "" : ",").Append("{\"id\":").Append(s.Number).Append(",\"x\":").Append(s.Bounds.Left - all.Left)
                    .Append(",\"y\":").Append(s.Bounds.Top - all.Top).Append(",\"w\":").Append(s.Bounds.Width).Append(",\"h\":").Append(s.Bounds.Height)
                    .Append(",\"primary\":").Append(s.Primary ? "true" : "false").Append("}");
            }
            return text.Append("]}").ToString();
        }

        // Point du bureau (pixels) -> coordonnées absolues de SendInput, de 0 à 65535 sur le bureau ENTIER
        // (MOUSEEVENTF_VIRTUALDESK), quel que soit l'écran montré.
        internal static void Absolute(Rectangle all, long px, long py, out int x, out int y)
        {
            x = (int)((px - all.Left) * 65535 / Math.Max(1, all.Width - 1));
            y = (int)((py - all.Top) * 65535 / Math.Max(1, all.Height - 1));
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct MonitorInfo
        {
            public int Size, Left, Top, Right, Bottom, WorkLeft, WorkTop, WorkRight, WorkBottom;
            public uint Flags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string Device;
        }
        delegate bool MonitorProc(IntPtr monitor, IntPtr dc, IntPtr rect, IntPtr data);
        [DllImport("user32.dll")] static extern bool EnumDisplayMonitors(IntPtr dc, IntPtr clip, MonitorProc each, IntPtr data);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern bool GetMonitorInfo(IntPtr monitor, ref MonitorInfo info);
    }
}
