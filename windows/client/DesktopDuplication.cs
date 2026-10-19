// Capture du bureau par duplication DXGI (Windows 8 et plus) : l'image est lue seulement quand l'écran change, avec la
// liste des zones modifiées. Le compagnon revient à GDI ailleurs : bureau sécurisé, plusieurs écrans, rotation, format HDR,
// pilote sans duplication. Appels COM par la table virtuelle : aucune dépendance (SharpDX…) à embarquer.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Runtime.InteropServices;

namespace Loco
{
    internal sealed class DesktopDuplication : IDisposable
    {
        const int NotFound = unchecked((int)0x887A0002), MoreData = unchecked((int)0x887A0003), WaitTimeout = unchecked((int)0x887A0027);
        const int FormatBgra = 87, FormatBgraSrgb = 91;
        static Guid factoryId = new Guid("770aae78-f26f-4dba-a829-253c83d1b387");
        static Guid output1Id = new Guid("00cddea8-939b-4b83-a340-a685226666cc");
        static Guid textureId = new Guid("6f15aaf2-d208-4e89-9ab4-489535d34f9c");

        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int EnumFn(IntPtr self, uint index, out IntPtr item);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int DescFn(IntPtr self, IntPtr desc);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate void VoidDescFn(IntPtr self, IntPtr desc);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int DuplicateFn(IntPtr self, IntPtr device, out IntPtr duplication);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int AcquireFn(IntPtr self, uint timeout, IntPtr info, out IntPtr resource);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int RectsFn(IntPtr self, uint size, IntPtr buffer, out uint required);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CallFn(IntPtr self);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CreateTextureFn(IntPtr self, IntPtr desc, IntPtr data, out IntPtr texture);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int MapFn(IntPtr self, IntPtr resource, uint sub, uint type, uint flags, IntPtr mapped);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate void UnmapFn(IntPtr self, IntPtr resource, uint sub);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate void CopyFn(IntPtr self, IntPtr destination, IntPtr source);

        // Indices dans les tables virtuelles (IUnknown 0-2, IDXGIObject 3-6, puis l'interface).
        static T Slot<T>(IntPtr obj, int index) where T : class
        {
            IntPtr table = Marshal.ReadIntPtr(obj);
            return (T)(object)Marshal.GetDelegateForFunctionPointer(Marshal.ReadIntPtr(table, index * IntPtr.Size), typeof(T));
        }
        static void Release(ref IntPtr obj) { if (obj != IntPtr.Zero) { Marshal.Release(obj); obj = IntPtr.Zero; } }

        IntPtr device, context, duplication, staging, info, mapped, frameDesc;
        readonly AcquireFn acquire; readonly RectsFn dirtyRects, moveRects; readonly CallFn releaseFrame, unmapDesktop; readonly DescFn mapDesktop;
        readonly MapFn map; readonly UnmapFn unmap; readonly CopyFn copy;
        readonly bool systemMemory;
        public readonly int Width, Height;
        bool hasImage;
        IntPtr rects = Marshal.AllocHGlobal(4096); uint rectsSize = 4096;
        readonly List<Rectangle> regions = new List<Rectangle>();

        /// <summary>Duplication de l'écran qui couvre exactement <paramref name="bounds"/> ; null si ce n'est pas possible ici.</summary>
        public static DesktopDuplication Open(Rectangle bounds)
        {
            IntPtr factory = IntPtr.Zero, adapter = IntPtr.Zero, output = IntPtr.Zero, desc = Marshal.AllocHGlobal(256);
            try
            {
                if (CreateDXGIFactory1(ref factoryId, out factory) < 0) return null;
                var enumAdapters = Slot<EnumFn>(factory, 12);
                for (uint a = 0; enumAdapters(factory, a, out adapter) != NotFound; a++)
                {
                    if (adapter == IntPtr.Zero) break;
                    var enumOutputs = Slot<EnumFn>(adapter, 7);
                    for (uint o = 0; enumOutputs(adapter, o, out output) != NotFound; o++)
                    {
                        if (output == IntPtr.Zero) break;
                        // DXGI_OUTPUT_DESC : nom (64 octets), coordonnées (RECT), rattachement, rotation.
                        if (Slot<DescFn>(output, 7)(output, desc) >= 0 && Marshal.ReadInt32(desc, 80) != 0 &&
                            Marshal.ReadInt32(desc, 64) == bounds.Left && Marshal.ReadInt32(desc, 68) == bounds.Top &&
                            Marshal.ReadInt32(desc, 72) == bounds.Right && Marshal.ReadInt32(desc, 76) == bounds.Bottom &&
                            Marshal.ReadInt32(desc, 84) <= 1)
                            return new DesktopDuplication(adapter, output, bounds.Width, bounds.Height);
                        Release(ref output);
                    }
                    Release(ref adapter);
                }
                return null;
            }
            finally { Release(ref output); Release(ref adapter); Release(ref factory); Marshal.FreeHGlobal(desc); }
        }

        DesktopDuplication(IntPtr adapter, IntPtr output, int width, int height)
        {
            Width = width; Height = height;
            info = Marshal.AllocHGlobal(64); mapped = Marshal.AllocHGlobal(32); frameDesc = Marshal.AllocHGlobal(64);
            IntPtr output1 = IntPtr.Zero, desc = Marshal.AllocHGlobal(64);
            try
            {
                int level;
                Check(D3D11CreateDevice(adapter, 0, IntPtr.Zero, 0, IntPtr.Zero, 0, 7, out device, out level, out context));
                Check(Marshal.QueryInterface(output, ref output1Id, out output1));
                Check(Slot<DuplicateFn>(output1, 22)(output1, device, out duplication));
                // DXGI_OUTDUPL_DESC : format de l'image (ModeDesc.Format) et image déjà en mémoire système.
                Slot<VoidDescFn>(duplication, 7)(duplication, desc);
                int format = Marshal.ReadInt32(desc, 16);
                if (format != FormatBgra && format != FormatBgraSrgb) throw new NotSupportedException("Format d'image " + format);
                systemMemory = Marshal.ReadInt32(desc, 32) != 0;
                acquire = Slot<AcquireFn>(duplication, 8); dirtyRects = Slot<RectsFn>(duplication, 9); moveRects = Slot<RectsFn>(duplication, 10);
                mapDesktop = Slot<DescFn>(duplication, 12); unmapDesktop = Slot<CallFn>(duplication, 13); releaseFrame = Slot<CallFn>(duplication, 14);
                map = Slot<MapFn>(context, 14); unmap = Slot<UnmapFn>(context, 15); copy = Slot<CopyFn>(context, 47);
                if (!systemMemory)
                {
                    // Texture de lecture (STAGING, accès CPU en lecture) à la taille du bureau.
                    int[] t = { width, height, 1, 1, format, 1, 0, 3, 0, 0x20000, 0 };
                    Marshal.Copy(t, 0, desc, t.Length);
                    Check(Slot<CreateTextureFn>(device, 5)(device, desc, IntPtr.Zero, out staging));
                }
            }
            catch { Dispose(); throw; }
            finally { Release(ref output1); Marshal.FreeHGlobal(desc); }
        }
        static void Check(int hr) { if (hr < 0) Marshal.ThrowExceptionForHR(hr); }

        /// <summary>1 : image mise à jour dans <paramref name="pixels"/> (BGRX, largeur × hauteur) et tuiles touchées marquées ;
        /// 0 : rien de neuf dans le délai ; -1 : duplication perdue (changement de bureau ou de mode), à recréer.</summary>
        public int Capture(byte[] pixels, int timeout, int tile, bool[] dirty)
        {
            IntPtr resource;
            int hr = acquire(duplication, (uint)Math.Max(0, timeout), info, out resource);
            if (hr == WaitTimeout) return 0;
            if (hr < 0) return -1;
            bool released = false;
            try
            {
                if (Marshal.ReadInt64(info, 0) == 0 && hasImage) return 0;   // seul le pointeur a bougé
                regions.Clear();
                bool all = !hasImage;
                if (!all && !(ReadRects(dirtyRects, 16, 0) && ReadRects(moveRects, 24, 8))) all = true;
                if (all) { regions.Clear(); regions.Add(new Rectangle(0, 0, Width, Height)); }
                if (systemMemory)
                {
                    if (mapDesktop(duplication, mapped) < 0) return -1;
                    try
                    {
                        // Surface d'une autre taille (changement de mode en cours) : ne rien lire, la duplication est à recréer.
                        if (Marshal.ReadInt32(mapped, 0) < Width * 4) return -1;
                        CopyRegions(Marshal.ReadIntPtr(mapped, IntPtr.Size), Marshal.ReadInt32(mapped, 0), pixels);   // DXGI_MAPPED_RECT : Pitch, pBits
                    }
                    finally { unmapDesktop(duplication); }
                }
                else
                {
                    IntPtr texture;
                    if (Marshal.QueryInterface(resource, ref textureId, out texture) < 0) return -1;
                    try
                    {
                        // D3D11_TEXTURE2D_DESC de l'image reçue (largeur, hauteur en tête) : une autre taille signale un changement de mode.
                        Slot<VoidDescFn>(texture, 10)(texture, frameDesc);
                        if (Marshal.ReadInt32(frameDesc, 0) != Width || Marshal.ReadInt32(frameDesc, 4) != Height) return -1;
                        copy(context, staging, texture);
                    }
                    finally { Marshal.Release(texture); }
                    releaseFrame(duplication); released = true;   // libérer l'image au plus tôt : la copie GPU est déjà demandée
                    if (map(context, staging, 0, 1, 0, mapped) < 0) return -1;
                    try { CopyRegions(Marshal.ReadIntPtr(mapped, 0), Marshal.ReadInt32(mapped, IntPtr.Size), pixels); }
                    finally { unmap(context, staging, 0); }
                }
                int columns = (Width + tile - 1) / tile;
                foreach (var r in regions)
                    for (int ty = r.Top / tile; ty <= (r.Bottom - 1) / tile; ty++)
                        for (int tx = r.Left / tile; tx <= (r.Right - 1) / tile; tx++)
                            dirty[ty * columns + tx] = true;
                hasImage = true;
                return 1;
            }
            finally
            {
                if (resource != IntPtr.Zero) Marshal.Release(resource);
                if (!released) releaseFrame(duplication);
            }
        }
        // Zones modifiées (RECT) puis déplacées (point source + RECT destination), bornées à l'écran.
        bool ReadRects(RectsFn read, int size, int offset)
        {
            uint required;
            int hr = read(duplication, rectsSize, rects, out required);
            if (hr == MoreData)
            {
                Marshal.FreeHGlobal(rects); rectsSize = required + 1024; rects = Marshal.AllocHGlobal((int)rectsSize);
                hr = read(duplication, rectsSize, rects, out required);
            }
            if (hr < 0) return false;
            for (int p = 0; p + size <= required; p += size)
            {
                int left = Math.Max(0, Marshal.ReadInt32(rects, p + offset)), top = Math.Max(0, Marshal.ReadInt32(rects, p + offset + 4));
                int right = Math.Min(Width, Marshal.ReadInt32(rects, p + offset + 8)), bottom = Math.Min(Height, Marshal.ReadInt32(rects, p + offset + 12));
                if (right > left && bottom > top) regions.Add(Rectangle.FromLTRB(left, top, right, bottom));
            }
            return true;
        }
        void CopyRegions(IntPtr source, int pitch, byte[] pixels)
        {
            if (pixels.Length < Width * Height * 4) throw new InvalidOperationException("Tampon de capture trop petit");
            foreach (var r in regions)
                for (int y = r.Top; y < r.Bottom; y++)
                    Marshal.Copy(IntPtr.Add(source, y * pitch + r.Left * 4), pixels, (y * Width + r.Left) * 4, r.Width * 4);
        }
        public void Dispose()
        {
            Release(ref staging); Release(ref duplication); Release(ref context); Release(ref device);
            if (info != IntPtr.Zero) { Marshal.FreeHGlobal(info); info = IntPtr.Zero; }
            if (mapped != IntPtr.Zero) { Marshal.FreeHGlobal(mapped); mapped = IntPtr.Zero; }
            if (frameDesc != IntPtr.Zero) { Marshal.FreeHGlobal(frameDesc); frameDesc = IntPtr.Zero; }
            if (rects != IntPtr.Zero) { Marshal.FreeHGlobal(rects); rects = IntPtr.Zero; }
        }
        [DllImport("dxgi.dll")] static extern int CreateDXGIFactory1(ref Guid riid, out IntPtr factory);
        [DllImport("d3d11.dll")] static extern int D3D11CreateDevice(IntPtr adapter, int driverType, IntPtr software, uint flags, IntPtr levels,
            uint count, uint sdk, out IntPtr device, out int level, out IntPtr context);
    }
}
