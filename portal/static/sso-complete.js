/* Nouvelle navigation après le retour SSO : le cookie principal conserve SameSite=Strict. */
const destination = document.getElementById('sso-continue');
if (destination && destination.origin === window.location.origin) window.location.replace(destination.href);
