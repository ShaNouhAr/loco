/* La fenêtre définit le bureau demandé ; le pilote Windows confirme la taille réellement disponible. */
export function resolutionFor(nativeWidth, nativeHeight, viewWidth, viewHeight, mode) {
  if (![nativeWidth, nativeHeight, viewWidth, viewHeight].every(n => Number.isFinite(n) && n > 0)) return null;
  if (mode === 'auto') return {width: Math.max(160, Math.min(4000, Math.floor(viewWidth))), height: Math.max(100, Math.min(2160, Math.floor(viewHeight)))};
  const ratio = Math.min(1, 4096 / nativeWidth, 2160 / nativeHeight);
  const width = Math.max(160, Math.round(nativeWidth * ratio));
  const height = Math.max(100, Math.round(nativeHeight * ratio));
  return {width, height};
}

export function resizePacket(width, height) {
  const packet = new Uint8Array(24), view = new DataView(packet.buffer);
  packet[0] = 251; packet[6] = 1;
  view.setUint16(2, width); view.setUint16(4, height);
  view.setUint16(16, width); view.setUint16(18, height);
  return packet;
}
