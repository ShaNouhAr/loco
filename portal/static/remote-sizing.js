/* En mode « Adaptée à la fenêtre », le bureau demandé suit la zone d'affichage ; le pilote Windows confirme la taille réellement disponible. */
export function resolutionFor(viewWidth, viewHeight) {
  if (![viewWidth, viewHeight].every(n => Number.isFinite(n) && n > 0)) return null;
  return {width: Math.max(160, Math.min(4096, Math.floor(viewWidth))),
          height: Math.max(100, Math.min(2160, Math.floor(viewHeight)))};
}

export function resizePacket(width, height) {
  const packet = new Uint8Array(24), view = new DataView(packet.buffer);
  packet[0] = 251; packet[6] = 1;
  view.setUint16(2, width); view.setUint16(4, height);
  view.setUint16(16, width); view.setUint16(18, height);
  return packet;
}
