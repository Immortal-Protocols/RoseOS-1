// Rose HiFi — icon set. Firefox, Thunderbird and VLC use the authorized artwork
// embedded in the former prototypes/rose-system-interactions at 9664372 (official sources via the
// Illustrator archive). Every other glyph here is an original procedural drawing.
(function () {
  'use strict';
  const ui = (body, w) => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="' + (w || 1.8) + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + '</svg>';
  const UI = {
    mic: ui('<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>'),
    micOff: ui('<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M4 4l16 16"/>'),
    end: ui('<path d="M6 6l12 12M18 6L6 18"/>', 2.2),
    grid: '<svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><rect x="3.5" y="3.5" width="7.5" height="7.5" rx="2.2"/><rect x="13" y="3.5" width="7.5" height="7.5" rx="2.2"/><rect x="3.5" y="13" width="7.5" height="7.5" rx="2.2"/><rect x="13" y="13" width="7.5" height="7.5" rx="2.2"/></svg>',
    back: ui('<path d="M15 5l-7 7 7 7"/>', 2.2),
    stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6.5" y="6.5" width="11" height="11" rx="2.4" fill="currentColor"/></svg>',
    check: ui('<path d="M5 12.5l4.5 4.5L19 7.5"/>', 2.4),
    alert: ui('<path d="M12 7v6M12 17h.01"/>', 2.6),
    phone: ui('<path d="M3.5 14.5c2.5-5 14.5-5 17 0l-3.5 2.2-2-3.4h-6l-2 3.4z"/>'),
    rose: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="6.5" fill="#c56f78"/><circle cx="10" cy="9.6" r="2.2" fill="#fff" opacity=".7"/></svg>',
  };

  // App artwork: 48×48, light from upper left to match the tray.
  const art = (body) => '<svg viewBox="0 0 48 48" aria-hidden="true">' + body + '</svg>';
  const ART = {
    camera: art('<rect x="5" y="12" width="38" height="28" rx="8" fill="#e9edf2"/><rect x="5" y="12" width="38" height="28" rx="8" fill="none" stroke="#9aa6b5"/><rect x="15" y="8" width="12" height="6" rx="2" fill="#c9d1db"/><circle cx="24" cy="26" r="10" fill="#2a2f37"/><circle cx="24" cy="26" r="6.5" fill="#0d1117"/><circle cx="24" cy="26" r="4" fill="#24406a"/><circle cx="21.8" cy="23.6" r="1.8" fill="#fff" opacity=".85"/><circle cx="36" cy="18" r="1.6" fill="#ef6b5c"/>'),
    settings: art('<g transform="translate(24 24)"><g fill="#a7b1bd">' + [0, 45, 90, 135, 180, 225, 270, 315].map((a) => '<rect x="-3.6" y="-19" width="7.2" height="9" rx="2" transform="rotate(' + a + ')"/>').join('') + '</g><circle r="13.5" fill="#c3cbd5"/><circle r="13.5" fill="none" stroke="#8994a3"/><circle r="6" fill="#eef2f6"/><circle r="6" fill="none" stroke="#7d8897"/></g>'),
    photos: art('<rect x="6" y="9" width="36" height="30" rx="7" fill="#fff"/><rect x="9" y="12" width="30" height="24" rx="4" fill="#8fc3f0"/><path d="M9 31l9-9 7 7 4-4 10 10v1a4 4 0 0 1-4 4H13a4 4 0 0 1-4-4z" fill="#58a364"/><path d="M9 33l9-7 8 7 4-3 9 6v1a4 4 0 0 1-4 4H13a4 4 0 0 1-4-4z" fill="#3f8a4f"/><circle cx="31" cy="18" r="3.2" fill="#ffe08a"/>'),
    maps: art('<path d="M7 13l11-4 12 4 11-4v26l-11 4-12-4-11 4z" fill="#eef3ea"/><path d="M18 9v26M30 13v26" stroke="#d2dccc"/><path d="M7 28c8-1 10-8 18-9s10 4 16 2" stroke="#7fb3ec" stroke-width="5" fill="none"/><path d="M12 34c5-4 9-3 13-7s7-8 11-9" stroke="#2f6be6" stroke-width="2" stroke-dasharray="3 2.4" fill="none"/><path d="M36 9.5a5 5 0 0 1 5 5c0 4-5 8.5-5 8.5s-5-4.5-5-8.5a5 5 0 0 1 5-5z" fill="#e5533e"/><circle cx="36" cy="14.5" r="1.8" fill="#fff"/>'),
    notes: art('<rect x="9" y="6" width="30" height="36" rx="6" fill="#fffdf4"/><rect x="9" y="6" width="30" height="9" rx="6" fill="#ffd25e"/><rect x="9" y="11" width="30" height="4" fill="#ffd25e"/><path d="M15 22h18M15 28h18M15 34h11" stroke="#c7c0a6" stroke-width="2" stroke-linecap="round"/>'),
    calendar: art('<rect x="7" y="9" width="34" height="32" rx="7" fill="#fff"/><path d="M7 16a7 7 0 0 1 7-7h20a7 7 0 0 1 7 7v2H7z" fill="#e5533e"/><text x="24" y="36" text-anchor="middle" font-family="Inter,sans-serif" font-size="15" font-weight="600" fill="#20242c">29</text>'),
    files: art('<path d="M6 14a4 4 0 0 1 4-4h9l4 4h15a4 4 0 0 1 4 4v18a4 4 0 0 1-4 4H10a4 4 0 0 1-4-4z" fill="#5d93ef"/><path d="M6 19a4 4 0 0 1 4-4h28a4 4 0 0 1 4 4v17a4 4 0 0 1-4 4H10a4 4 0 0 1-4-4z" fill="#8bb6fb"/>'),
    clock: art('<circle cx="24" cy="24" r="17" fill="#1f2530"/><circle cx="24" cy="24" r="14.5" fill="#fbfcfe"/><path d="M24 14v10l7 4" stroke="#1f2530" stroke-width="2.4" stroke-linecap="round" fill="none"/><circle cx="24" cy="24" r="1.8" fill="#e5533e"/>'),
    weather: art('<circle cx="19" cy="19" r="8" fill="#ffcf56"/><path d="M15 38a7 7 0 0 1 0-14 9 9 0 0 1 17-2 7 7 0 0 1 2 16z" fill="#fff"/><path d="M15 38a7 7 0 0 1 0-14 9 9 0 0 1 17-2 7 7 0 0 1 2 16z" fill="none" stroke="#c9d6e6"/>'),
    messages: art('<path d="M8 21c0-8 7-13 16-13s16 5 16 13-7 13-16 13c-2 0-4-.3-5.6-.8L11 37l1.6-6.4C9.7 28.3 8 25 8 21z" fill="#46b768"/><path d="M17 20h14M17 25h9" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/>'),
    music: art('<rect x="7" y="7" width="34" height="34" rx="9" fill="#f05a7e"/><path d="M20 32V15l14-3v17" stroke="#fff" stroke-width="2.4" fill="none" stroke-linejoin="round"/><circle cx="17" cy="32" r="3.6" fill="#fff"/><circle cx="31" cy="29" r="3.6" fill="#fff"/>'),
    calculator: art('<rect x="10" y="6" width="28" height="36" rx="6" fill="#2a303a"/><rect x="14" y="10" width="20" height="8" rx="2" fill="#cfe3d4"/>' + [0, 1, 2].map((r) => [0, 1, 2].map((c) => '<rect x="' + (14 + c * 7) + '" y="' + (21 + r * 6.5) + '" width="5.5" height="5" rx="1.4" fill="' + (c === 2 ? '#f39a3d' : '#5b6575') + '"/>').join('')).join('')),
    contacts: art('<rect x="8" y="6" width="32" height="36" rx="7" fill="#e9edf3"/><circle cx="24" cy="20" r="6.5" fill="#8a97aa"/><path d="M13 36c1.5-6 6-8.5 11-8.5s9.5 2.5 11 8.5" fill="#8a97aa"/>'),
    editor: art('<rect x="6" y="8" width="36" height="32" rx="7" fill="#22293a"/><path d="M15 19l-4 5 4 5M33 19l4 5-4 5M27 16l-6 17" stroke="#8fc0ff" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'),
    phone: art('<rect x="7" y="7" width="34" height="34" rx="10" fill="#3fb25f"/><path d="M17 13c1-.6 2.4-.3 3 .8l2 3.6c.5 1 .2 2.2-.7 2.8l-1.5 1c1 2.6 3.4 5 6 6l1-1.5c.6-.9 1.8-1.2 2.8-.7l3.6 2c1.1.6 1.4 2 .8 3l-1.2 1.8c-.9 1.3-2.6 2-4.1 1.5C21 31.8 16.2 27 14.6 19.4c-.4-1.5.2-3.2 1.5-4.1z" fill="#fff"/>'),
  };
  const RASTER = { firefox: '%%ICON_FIREFOX%%', thunderbird: '%%ICON_THUNDERBIRD%%', vlc: '%%ICON_VLC%%' };

  function appArt(id) {
    if (RASTER[id]) return '<img alt="" draggable="false" src="' + RASTER[id] + '">';
    return ART[id] || ART.files;
  }
  window.RoseIcons = { UI, appArt };
})();
