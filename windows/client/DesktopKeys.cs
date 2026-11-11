// Pavé numérique du bureau distant : keysyms X11 (XK_KP_*) -> touches virtuelles Windows.
// Sans cette table, ces keysyms étaient pris pour des caractères Unicode (U+FFB0…) et tapaient des glyphes sans rapport.
namespace Loco
{
    internal static class DesktopKeys
    {
        // Touche virtuelle d'un keysym du pavé numérique, ou 0 si ce n'en est pas un. Les chiffres donnent VK_NUMPAD0-9,
        // qui tapent le chiffre quel que soit l'état de Verr. num. du poste ; les keysyms de navigation (Verr. num. éteint
        // côté navigateur) donnent la touche de navigation. extended : Entrée et « / » du pavé (KEYEVENTF_EXTENDEDKEY).
        internal static ushort Keypad(uint sym, out bool extended)
        {
            extended = sym == 0xff8d || sym == 0xffaf;
            if (sym >= 0xffb0 && sym <= 0xffb9) return (ushort)(0x60 + (sym - 0xffb0));
            switch (sym)
            {
                case 0xff80: return 0x20;   // espace
                case 0xff89: return 0x09;   // tabulation
                case 0xff8d: return 0x0d;   // Entrée
                case 0xff95: return 0x24;   // Origine (7)
                case 0xff96: return 0x25;   // gauche (4)
                case 0xff97: return 0x26;   // haut (8)
                case 0xff98: return 0x27;   // droite (6)
                case 0xff99: return 0x28;   // bas (2)
                case 0xff9a: return 0x21;   // page précédente (9)
                case 0xff9b: return 0x22;   // page suivante (3)
                case 0xff9c: return 0x23;   // Fin (1)
                case 0xff9d: return 0x0c;   // touche 5 sans Verr. num. (VK_CLEAR)
                case 0xff9e: return 0x2d;   // Inser (0)
                case 0xff9f: return 0x2e;   // Suppr (.)
                case 0xffaa: return 0x6a;   // *
                case 0xffab: return 0x6b;   // +
                case 0xffac: return 0x6e;   // séparateur décimal « , » : la même touche que « . »
                case 0xffad: return 0x6d;   // -
                case 0xffae: return 0x6e;   // .
                case 0xffaf: return 0x6f;   // /
                default: return 0;
            }
        }
    }
}
