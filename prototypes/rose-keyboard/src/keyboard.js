// Rose Keyboard (F10 study) — pure text/keyboard model. No DOM; runs in the page and in Node.
// One editable field: text, selection [a, b] (a === b is a caret), shift state, symbol page,
// suggestions, autocorrect with one-step revert, word deletion, clipboard, and undo/redo.
(function (root) {
  'use strict';

  // Common words, most frequent first (rank = weight). Small on purpose: a prototype lexicon.
  const WORDS = ('the I to you and a it is that of in for on be have we are this my me what with ' +
    'so at your just can do not was but if will all get up about out how now like know there ' +
    'one when go see time good back think they then here more today tomorrow Saturday Sunday ' +
    'morning afternoon evening tonight great thanks thank sounds perfect okay yes no maybe sure ' +
    'meet meeting garden gate ten eleven noon walk river lunch coffee café later soon still ' +
    'would could should want need going come coming leave left right where why who which ' +
    'from been has had did does make made take took let lets love lovely nice happy hope ' +
    'plan plans weekend week day days call calling talk talked message write wrote read ' +
    'Jamie Rose notes list book books bring open opens close closed early late quiet quieter ' +
    'there their they\'re don\'t can\'t won\'t I\'m I\'ll I\'ve you\'re that\'s it\'s ' +
    'receive address separate definitely tomorrow\'s keyboard typing suggestion ' +
    'home phone place places after before again also around away really very well work ' +
    'hello hi hey way my OK on lunch dinner tea pack call text soon reply ready').split(/\s+/).filter(Boolean);
  const RANK = new Map(WORDS.map((w, i) => [w.toLowerCase(), i]));
  const CANON = new Map(WORDS.map((w) => [w.toLowerCase(), w]));

  // Autocorrect only for unambiguous typos; anything else is left exactly as typed.
  const CORRECT = {
    teh: 'the', adn: 'and', dont: "don't", im: "I'm", ive: "I've",
    youre: "you're", thats: "that's", recieve: 'receive', adress: 'address',
    tommorow: 'tomorrow', tomorow: 'tomorrow', seperate: 'separate', definately: 'definitely',
    i: 'I', jamie: 'Jamie', saturday: 'Saturday', sunday: 'Sunday', rose: 'Rose',
  };
  // Valid words that are often meant as contractions: offered as the best suggestion, never applied
  // automatically (ill, cant and wont are real words).
  const SUGGEST = { ill: "I'll", cant: "can't", wont: "won't", well: "we'll", were: "we're", hell: "he'll", shell: "she'll" };
  // Next-word predictions after a completed word (empty current word).
  const NEXT = {
    '': ['I', 'The', 'Thanks'], see: ['you', 'the', 'it'], at: ['ten', 'the', 'noon'],
    the: ['garden', 'river', 'gate'], sounds: ['good', 'great', 'perfect'], thank: ['you', 'you!', 'you so'],
    i: ['think', "I'll", 'can'], "i'll": ['be', 'bring', 'call'], meet: ['at', 'you', 'there'],
    on: ['Saturday', 'Sunday', 'the'], by: ['the', 'ten', 'noon'], good: ['morning', 'idea', 'plan'],
  };

  const SENT_END = /[.!?]\s+$/;
  const WORD_CH = /[\p{L}\p{N}'’]/u;

  function initial(opts) {
    const text = (opts && opts.text) || '';
    const s = {
      text, a: text.length, b: text.length, multiline: !!(opts && opts.multiline),
      shift: 'off', page: 'letters', undo: [], redo: [], clip: '', lastAuto: null,
      lastShiftAt: -1e9, lastSpaceAt: -1e9, typingRun: false, events: [],
    };
    autoCap(s);
    return s;
  }

  // ---------- helpers ----------
  const snap = (s) => ({ text: s.text, a: s.a, b: s.b });
  function pushUndo(s, coalesce) {
    if (coalesce && s.typingRun) return;
    s.undo.push(snap(s)); if (s.undo.length > 100) s.undo.shift();
    s.redo = []; s.typingRun = !!coalesce;
  }
  const lo = (s) => Math.min(s.a, s.b), hi = (s) => Math.max(s.a, s.b);
  function replace(s, from, to, str) {
    s.text = s.text.slice(0, from) + str + s.text.slice(to);
    s.a = s.b = from + str.length;
  }
  // The word being typed: word characters immediately before the caret.
  function currentWord(s) {
    if (s.a !== s.b) return { start: s.a, end: s.a, word: '' };
    let i = s.a; while (i > 0 && WORD_CH.test(s.text[i - 1])) i--;
    return { start: i, end: s.a, word: s.text.slice(i, s.a) };
  }
  function prevWord(s, before) {
    const t = s.text.slice(0, before).replace(/\s+$/, '');
    const m = t.match(/([\p{L}\p{N}'’]+)[^\p{L}\p{N}'’]*$/u);
    return m ? m[1].toLowerCase() : '';
  }
  function autoCap(s) {
    if (s.shift === 'lock') return;
    const before = s.text.slice(0, lo(s));
    s.shift = before.length === 0 || SENT_END.test(before) || /\n\s*$/.test(before) ? 'once' : 'off';
  }
  function matchCase(word, typed) {
    if (typed.length > 1 && typed === typed.toUpperCase() && /[A-Z]/.test(typed)) return word.toUpperCase();
    if (typed[0] && typed[0] === typed[0].toUpperCase() && /[A-Z]/.test(typed[0])) return word[0].toUpperCase() + word.slice(1);
    return word;
  }
  const log = (s, e) => { s.events.push(e); if (s.events.length > 40) s.events.shift(); };

  // ---------- typing ----------
  function type(s, ch) {
    const up = s.shift !== 'off' && /[a-z]/.test(ch) ? ch.toUpperCase() : ch;
    pushUndo(s, WORD_CH.test(up));
    if (/[.,!?;:]/.test(up) && s.a === s.b) correctCurrent(s);
    replace(s, lo(s), hi(s), up);
    s.lastAuto = null;
    if (s.shift === 'once') s.shift = 'off';
    if (!WORD_CH.test(up)) { s.typingRun = false; autoCap(s); }
    log(s, 'type ' + up);
  }

  // Apply an unambiguous correction to the word before the caret (space, Return, punctuation).
  function correctCurrent(s) {
    const cw = currentWord(s);
    const fix = cw.word && CORRECT[cw.word.toLowerCase()];
    if (!fix || fix === cw.word || s.revertedWord === cw.word) return null;
    const to = cw.word.length === 1 ? fix : matchCase(fix, cw.word);
    replace(s, cw.start, cw.end, to);
    log(s, 'autocorrect ' + cw.word + '→' + to);
    return { from: cw.word, to };
  }

  function space(s, now) {
    now = now || 0;
    // Double-tap space after a word: ". " (sentence end), then auto-capitalise.
    if (s.a === s.b && now - s.lastSpaceAt < 450 && / $/.test(s.text.slice(0, s.a)) && WORD_CH.test(s.text[s.a - 2] || '')) {
      pushUndo(s); replace(s, s.a - 1, s.a, '. '); s.lastSpaceAt = -1e9; s.lastAuto = null; autoCap(s); log(s, 'period'); return;
    }
    pushUndo(s);
    const fixed = s.a === s.b ? correctCurrent(s) : null;
    replace(s, lo(s), hi(s), ' ');
    s.lastAuto = fixed ? { from: fixed.from, to: fixed.to, at: s.a } : null;
    if (!fixed) log(s, 'space');
    s.lastSpaceAt = now; s.typingRun = false; autoCap(s);
  }

  function backspace(s) {
    // Right after an autocorrect, backspace restores exactly what was typed.
    if (s.lastAuto && s.a === s.b && s.a === s.lastAuto.at) {
      const { from, to } = s.lastAuto;
      pushUndo(s); replace(s, s.a - to.length - 1, s.a, from); s.lastAuto = null; s.revertedWord = from;
      log(s, 'revert ' + to + '→' + from); autoCap(s); return;
    }
    s.lastAuto = null;
    if (s.a !== s.b) { pushUndo(s); replace(s, lo(s), hi(s), ''); log(s, 'delete selection'); autoCap(s); return; }
    if (s.a === 0) return;
    pushUndo(s, true);
    const cp = s.text.codePointAt(s.a - 2);
    const n = cp > 0xffff && s.a >= 2 ? 2 : 1;
    replace(s, s.a - n, s.a, ''); autoCap(s); log(s, 'backspace');
  }

  function deleteWord(s) {
    s.lastAuto = null;
    if (s.a !== s.b) return backspace(s);
    let i = s.a; while (i > 0 && /\s/.test(s.text[i - 1])) i--;
    if (i > 0 && WORD_CH.test(s.text[i - 1])) { while (i > 0 && WORD_CH.test(s.text[i - 1])) i--; }
    else if (i > 0) i--;
    if (i === s.a) return;
    pushUndo(s); replace(s, i, s.a, ''); autoCap(s); log(s, 'delete word');
  }

  // Return: newline in multi-line fields; in single-purpose fields the IME action (Send) fires.
  function enter(s) {
    if (s.multiline) { pushUndo(s); if (s.a === s.b) correctCurrent(s); replace(s, lo(s), hi(s), '\n'); s.lastAuto = null; s.typingRun = false; autoCap(s); log(s, 'newline'); return null; }
    if (s.a === s.b) correctCurrent(s);
    const sent = s.text.trim();
    if (!sent) return null;
    pushUndo(s); s.text = ''; s.a = s.b = 0; s.lastAuto = null; autoCap(s); log(s, 'send');
    return sent;
  }

  // ---------- shift & pages ----------
  function shiftTap(s, now) {
    now = now || 0;
    if (s.page !== 'letters') return;
    if (s.shift === 'lock') s.shift = 'off';
    else if (s.shift === 'once' && now - s.lastShiftAt < 350) s.shift = 'lock';
    else s.shift = s.shift === 'off' ? 'once' : 'off';
    s.lastShiftAt = now; log(s, 'shift ' + s.shift);
  }
  function setPage(s, page) { s.page = page; log(s, 'page ' + page); if (page === 'letters') autoCap(s); }

  // ---------- suggestions ----------
  // Three slots. While typing: [typed-as-is, best, next]; best is a correction when one applies.
  // Between words: next-word predictions. A just-reverted word is never re-corrected.
  function suggestions(s) {
    const cw = currentWord(s);
    if (s.a !== s.b) return [];
    if (!cw.word) {
      const p = NEXT[prevWord(s, s.a)] || (s.text.trim() ? ['the', 'and', 'I'] : NEXT['']);
      const cap = s.shift !== 'off';
      return p.slice(0, 3).map((w) => ({ text: cap ? w[0].toUpperCase() + w.slice(1) : w, kind: 'predict' }));
    }
    const low = cw.word.toLowerCase();
    const fix = CORRECT[low] && s.revertedWord !== cw.word ? matchCase(CORRECT[low], cw.word) : null;
    const pre = WORDS.filter((w) => w.toLowerCase().startsWith(low) && w.toLowerCase() !== low)
      .sort((x, y) => RANK.get(x.toLowerCase()) - RANK.get(y.toLowerCase()))
      .map((w) => matchCase(CANON.get(w.toLowerCase()), cw.word));
    const exact = CANON.has(low) ? matchCase(CANON.get(low), cw.word) : null;
    const alt = !fix && SUGGEST[low] ? (low === 'ill' ? "I'll" : matchCase(SUGGEST[low], cw.word)) : null;
    const best = fix || alt || exact || pre[0] || null;
    const out = [{ text: cw.word, kind: 'typed' }];
    if (best && best !== cw.word) out.push({ text: best, kind: fix ? 'correct' : 'suggest' });
    for (const w of pre) { if (out.length >= 3) break; if (!out.some((o) => o.text === w)) out.push({ text: w, kind: 'suggest' }); }
    // Slot order on the strip: typed | best | next (best in the middle).
    return out.length === 3 ? [out[0], out[1], out[2]] : out;
  }
  function pick(s, word) {
    const cw = currentWord(s);
    pushUndo(s);
    replace(s, cw.start, cw.end, word + ' ');
    s.lastAuto = null; s.revertedWord = null; s.typingRun = false; autoCap(s);
    log(s, 'pick ' + word);
  }

  // ---------- cursor, selection, clipboard, undo ----------
  function setSel(s, a, b) {
    const n = s.text.length;
    s.a = Math.max(0, Math.min(n, a)); s.b = Math.max(0, Math.min(n, b === undefined ? a : b));
    s.lastAuto = null; s.typingRun = false; s.revertedWord = null; autoCap(s);
  }
  function move(s, delta, extend) {
    if (extend) setSel(s, s.a, s.b + delta);
    else if (s.a !== s.b) { const p = delta < 0 ? lo(s) : hi(s); setSel(s, p); }
    else setSel(s, s.a + delta);
  }
  function selectWord(s, at) {
    let i = Math.max(0, Math.min(s.text.length, at)), j = i;
    while (i > 0 && WORD_CH.test(s.text[i - 1])) i--;
    while (j < s.text.length && WORD_CH.test(s.text[j])) j++;
    if (i === j && j < s.text.length) j++;
    setSel(s, i, j); log(s, 'select word');
  }
  function selectAll(s) { setSel(s, 0, s.text.length); log(s, 'select all'); }
  const selected = (s) => s.text.slice(lo(s), hi(s));
  function copy(s) { if (s.a !== s.b) { s.clip = selected(s); log(s, 'copy'); } }
  function cut(s) { if (s.a !== s.b) { s.clip = selected(s); pushUndo(s); replace(s, lo(s), hi(s), ''); autoCap(s); log(s, 'cut'); } }
  function paste(s) { if (s.clip) { pushUndo(s); replace(s, lo(s), hi(s), s.clip); s.lastAuto = null; autoCap(s); log(s, 'paste'); } }
  function undo(s) {
    const u = s.undo.pop(); if (!u) return;
    s.redo.push(snap(s)); Object.assign(s, u); s.lastAuto = null; s.typingRun = false; autoCap(s); log(s, 'undo');
  }
  function redo(s) {
    const r = s.redo.pop(); if (!r) return;
    s.undo.push(snap(s)); Object.assign(s, r); s.lastAuto = null; s.typingRun = false; autoCap(s); log(s, 'redo');
  }

  // ---------- layouts ----------
  const LAYOUTS = {
    letters: [
      'q w e r t y u i o p'.split(' '),
      'a s d f g h j k l'.split(' '),
      ['shift', ...'z x c v b n m'.split(' '), 'bksp'],
      ['?123', ',', 'space', '.', 'action'],
    ],
    sym1: [
      '1 2 3 4 5 6 7 8 9 0'.split(' '),
      '@ # $ _ & - + ( ) /'.split(' '),
      ['=\\<', '*', '"', "'", ':', ';', '!', '?', 'bksp'],
      ['ABC', ',', 'space', '.', 'action'],
    ],
    sym2: [
      '~ ` | • √ π ÷ × ¶ ∆'.split(' '),
      '£ ¢ € ¥ ^ ° = { } \\'.split(' '),
      ['?123', '%', '©', '®', '™', '✓', '[', ']', 'bksp'],
      ['ABC', '<', 'space', '>', 'action'],
    ],
  };

  const api = { SUGGEST, initial, type, space, backspace, deleteWord, enter, shiftTap, setPage, suggestions, pick,
    setSel, move, selectWord, selectAll, copy, cut, paste, undo, redo, currentWord, LAYOUTS, CORRECT };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.RoseKeys = api;
})(typeof window !== 'undefined' ? window : globalThis);
