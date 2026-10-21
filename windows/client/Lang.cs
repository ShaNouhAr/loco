// Langue de l'agent : anglais, sauf si Windows est en français (langue d'affichage de la session, ou du compte
// SYSTEM pour le service). Lang.T(fr, en) choisit le texte vu par l'utilisateur du poste ou renvoyé au portail.
using System;
using System.Globalization;

namespace Loco
{
    static class Lang
    {
        public static readonly bool En =
            !CultureInfo.CurrentUICulture.TwoLetterISOLanguageName.Equals("fr", StringComparison.OrdinalIgnoreCase);

        public static string T(string fr, string en) { return En ? en : fr; }
    }
}
