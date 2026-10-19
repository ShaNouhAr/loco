// Résolution temporaire du bureau Windows : modes du pilote, sans écriture dans le registre.
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;

namespace Loco
{
    internal sealed class DesktopResolution : IDisposable
    {
        Mutex lease;
        bool ownsLease, changed;
        DisplayMode original, applied;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        internal struct DisplayMode
        {
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string DeviceName;
            public ushort SpecVersion, DriverVersion, Size, DriverExtra;
            public uint Fields;
            public int X, Y;
            public uint Orientation, FixedOutput;
            public short Color, Duplex, YResolution, TTOption, Collate;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string FormName;
            public ushort LogPixels;
            public uint BitsPerPixel, Width, Height, Flags, Frequency;
            public uint ICMMethod, ICMIntent, MediaType, DitherType, Reserved1, Reserved2, PanningWidth, PanningHeight;
        }

        static DisplayMode NewMode() { return new DisplayMode { Size = (ushort)Marshal.SizeOf(typeof(DisplayMode)) }; }
        static bool Current(out DisplayMode mode) { mode = NewMode(); return EnumDisplaySettings(null, -1, ref mode); }
        static bool Same(DisplayMode a, DisplayMode b)
        { return a.Width == b.Width && a.Height == b.Height && a.Orientation == b.Orientation && a.Frequency == b.Frequency; }

        // Plus petit = meilleur. Le navigateur ajuste le bureau à la zone en conservant son rapport :
        // d'abord la part de la zone remplie (un 21:9 pour une fenêtre très large), puis l'écart à 100 %.
        internal static double Score(int width, int height, int targetWidth, int targetHeight)
        {
            double scale = Math.Min((double)targetWidth / width, (double)targetHeight / height);
            double fill = scale * scale * width * height / ((double)targetWidth * targetHeight);
            return (1 - fill) + 0.05 * Math.Abs(Math.Log(scale));
        }

        public bool Resize(int width, int height)
        {
            // Une session conserve la main sur le mode physique. Les autres restent en ajustement local.
            // Ne pas déplacer les écrans d'un poste multi-moniteur.
            if (GetSystemMetrics(80) != 1) return false;
            if (!ownsLease)
            {
                if (lease == null) lease = new Mutex(false, @"Local\loco-desktop-resolution");
                try { ownsLease = lease.WaitOne(0); } catch (AbandonedMutexException) { ownsLease = true; }
                if (!ownsLease) return false;
                if (!Current(out original)) { lease.ReleaseMutex(); ownsLease = false; return false; }
            }
            DisplayMode current;
            if (!Current(out current)) return false;
            // Ne pas annuler un changement fait depuis les paramètres Windows pendant la session.
            if (changed && !Same(current, applied)) { original = current; changed = false; }
            if (current.Width == width && current.Height == height) return true;

            var candidates = new List<DisplayMode>();
            for (int i = 0; i < 4096; i++)
            {
                var mode = NewMode();
                if (!EnumDisplaySettings(null, i, ref mode)) break;
                if (mode.Width >= 640 && mode.Height >= 480 && mode.Width <= 4096 && mode.Height <= 2160 &&
                    mode.BitsPerPixel == current.BitsPerPixel && mode.Orientation == current.Orientation && (mode.Flags & 2) == 0)
                    candidates.Add(mode);
            }
            candidates.Sort((a, b) => {
                int order = Score((int)a.Width, (int)a.Height, width, height).CompareTo(Score((int)b.Width, (int)b.Height, width, height));
                return order != 0 ? order : Math.Abs((int)a.Frequency - (int)current.Frequency).CompareTo(Math.Abs((int)b.Frequency - (int)current.Frequency));
            });
            // Les pilotes virtuels acceptent parfois aussi une taille qui n'est pas dans leur liste.
            var exact = current; exact.Width = (uint)Math.Max(640, width); exact.Height = (uint)Math.Max(480, height);
            exact.Fields = 0x180000; exact.DriverExtra = 0;
            candidates.Insert(0, exact);
            foreach (var candidate in candidates)
            {
                var mode = candidate; mode.DriverExtra = 0;
                if (Same(mode, current)) return true;
                if (ChangeDisplaySettingsEx(null, ref mode, IntPtr.Zero, 2, IntPtr.Zero) != 0) continue; // CDS_TEST
                if (ChangeDisplaySettingsEx(null, ref mode, IntPtr.Zero, 0, IntPtr.Zero) != 0) continue;
                if (!Current(out applied)) applied = mode;
                changed = !Same(original, applied);
                return true;
            }
            return false;
        }

        public void Dispose()
        {
            try
            {
                DisplayMode current;
                if (ownsLease && changed && Current(out current) && Same(current, applied))
                {
                    original.DriverExtra = 0;
                    ChangeDisplaySettingsEx(null, ref original, IntPtr.Zero, 0, IntPtr.Zero);
                }
            }
            finally
            {
                if (ownsLease) { lease.ReleaseMutex(); ownsLease = false; }
                if (lease != null) { lease.Dispose(); lease = null; }
            }
        }

        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern bool EnumDisplaySettings(string device, int index, ref DisplayMode mode);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int ChangeDisplaySettingsEx(string device, ref DisplayMode mode, IntPtr window, uint flags, IntPtr parameter);
        [DllImport("user32.dll")] static extern int GetSystemMetrics(int index);
    }
}
