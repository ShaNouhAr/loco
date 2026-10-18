/* Modèles de configuration ; les secrets ne passent jamais par ce script. */
const preset = document.getElementById('sso-preset');
if (preset) {
  const update = () => {
    const option = preset.selectedOptions[0];
    document.getElementById('sso-label').value = option.textContent;
    document.getElementById('sso-issuer').value = option.dataset.issuer;
    document.getElementById('sso-auth-method').value = ['entra', 'google', 'github'].includes(preset.value)
      ? 'client_secret_post' : 'client_secret_basic';
  };
  preset.addEventListener('change', update);
  update();
}
