export function canStartCall(consent: boolean, voiceReady: boolean): boolean {
  return consent && voiceReady;
}
