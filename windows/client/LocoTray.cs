// Compagnon non privilégié de loco-client : affiche exclusivement l'état publié par le service.
// Il ne contacte pas le serveur, ne lit pas les jetons et ne lance aucune action d'administration.
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace Loco
{
    public static class TrayProgram
    {
        [STAThread]
        public static int Main(string[] args)
        {
            if (args.Length == 1 && args[0] == "--status")
            {
                Console.WriteLine(new JavaScriptSerializer().Serialize(DesktopStatus.Load()));
                return 0;
            }
            if (args.Length != 0) return 2;
            bool created;
            using (var singleton = new Mutex(true, @"Local\loco.Tray", out created))
            {
                if (!created) return 0;
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new TrayContext());
            }
            return 0;
        }
    }

    sealed class TrayContext : ApplicationContext
    {
        [DllImport("user32.dll")] static extern bool DestroyIcon(IntPtr icon);
        readonly NotifyIcon tray = new NotifyIcon();
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        readonly ToolStripMenuItem summary = new ToolStripMenuItem { Enabled = false };
        DesktopStatus status = new DesktopStatus();
        Icon icon;
        Form details;
        Label description;
        string lastColor = "";
        bool disposed;

        public TrayContext()
        {
            var menu = new ContextMenuStrip();
            menu.Items.Add(new ToolStripMenuItem(Lang.T("loco · État du poste", "loco · Machine status")) { Enabled = false });
            menu.Items.Add(summary);
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(Lang.T("Détails…", "Details…"), null, delegate { ShowDetails(); });
            tray.ContextMenuStrip = menu;
            tray.DoubleClick += delegate { ShowDetails(); };
            Refresh();
            tray.Visible = true;
            timer.Interval = 10000;
            timer.Tick += delegate
            {
                if (!File.Exists(Application.ExecutablePath)) { ExitThread(); return; }
                Refresh();
            };
            timer.Start();
        }

        string Heading()
        {
            return status.Color == "green" ? Lang.T("À jour", "Up to date") : status.Color == "red" ? Lang.T("Beaucoup de mises à jour", "Many updates") : status.Color == "orange" ? Lang.T("Mises à jour disponibles", "Updates available") : Lang.T("Informations indisponibles", "Status unavailable");
        }

        string DetailsText()
        {
            string when = status.UpdatedAt == "" ? Lang.T("Aucun contact connu", "No check-in yet") : Lang.T("Dernier contact : ", "Last check-in: ") + DateTime.Parse(status.UpdatedAt).ToLocalTime().ToString("g");
            return Heading() + "\r\n\r\n" + status.Reason + "\r\n\r\n" + when +
                Lang.T("\r\n\r\nCet indicateur est informatif. Les mises à jour sont pilotées par votre administrateur depuis loco.", "\r\n\r\nThis indicator is informational. Updates are managed by your administrator from loco.");
        }

        void Refresh()
        {
            status = DesktopStatus.Load();
            string text = "loco · " + status.Reason;
            tray.Text = text.Length > 63 ? text.Substring(0, 60) + "…" : text;
            summary.Text = Heading();
            if (lastColor != status.Color)
            {
                Icon old = icon;
                icon = MakeIcon(status.Color);
                tray.Icon = icon;
                if (details != null && !details.IsDisposed) details.Icon = icon;
                if (old != null) old.Dispose();
                lastColor = status.Color;
            }
            if (description != null && !description.IsDisposed) description.Text = DetailsText();
        }

        static Icon MakeIcon(string color)
        {
            using (var bitmap = new Bitmap(32, 32))
            {
                using (var g = Graphics.FromImage(bitmap))
                using (var brush = new SolidBrush(color == "green" ? Color.FromArgb(46, 180, 105) : color == "red" ? Color.FromArgb(226, 69, 69) : color == "orange" ? Color.FromArgb(238, 160, 40) : Color.FromArgb(140, 140, 140)))
                using (var border = new Pen(Color.FromArgb(60, 60, 60), 2))
                {
                    g.SmoothingMode = SmoothingMode.AntiAlias;
                    g.Clear(Color.Transparent);
                    g.FillEllipse(brush, 3, 3, 26, 26);
                    g.DrawEllipse(border, 3, 3, 26, 26);
                    using (var font = new Font("Segoe UI", 15, FontStyle.Bold, GraphicsUnit.Pixel))
                    using (var format = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
                        g.DrawString("L", font, Brushes.White, new RectangleF(3, 3, 26, 26), format);
                }
                IntPtr handle = bitmap.GetHicon();
                try { using (var raw = Icon.FromHandle(handle)) return (Icon)raw.Clone(); }
                finally { DestroyIcon(handle); }
            }
        }

        void ShowDetails()
        {
            if (details != null && !details.IsDisposed) { details.Activate(); return; }
            details = new Form { Text = Lang.T("loco · État des mises à jour", "loco · Update status"), ClientSize = new Size(460, 260),
                StartPosition = FormStartPosition.CenterScreen, FormBorderStyle = FormBorderStyle.FixedDialog,
                MaximizeBox = false, MinimizeBox = false, Icon = icon };
            description = new Label { Text = DetailsText(), Dock = DockStyle.Fill, Padding = new Padding(22), AutoSize = false };
            var close = new Button { Text = Lang.T("Fermer", "Close"), Dock = DockStyle.Bottom, Height = 36, DialogResult = DialogResult.Cancel };
            close.Click += delegate { details.Close(); };
            details.Controls.Add(description);
            details.Controls.Add(close);
            details.CancelButton = close;
            details.Show();
        }

        protected override void ExitThreadCore()
        {
            if (!disposed)
            {
                disposed = true;
                timer.Stop(); timer.Dispose();
                tray.Visible = false;
                tray.ContextMenuStrip.Dispose(); tray.Dispose();
                if (details != null) details.Dispose();
                if (icon != null) icon.Dispose();
            }
            base.ExitThreadCore();
        }
    }
}
