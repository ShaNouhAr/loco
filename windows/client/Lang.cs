// Langue de l'agent : français si Windows est en français, espagnol si en espagnol, anglais sinon (langue
// d'affichage de la session, ou du compte SYSTEM pour le service). Lang.T(fr, en, es) choisit le texte vu par
// l'utilisateur du poste ou renvoyé au portail.
using System.Globalization;

namespace Loco
{
    static class Lang
    {
        static readonly string Code =
            CultureInfo.CurrentUICulture.TwoLetterISOLanguageName.ToLowerInvariant();

        public static string T(string fr, string en, string es)
        {
            if (Code == "fr") return fr;
            if (Code == "es") return es;
            return en;
        }

        public static string T(string fr, string en) { return T(fr, en, en); }
    }
}
