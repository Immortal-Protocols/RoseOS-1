// Rose Keyboard (F10) — model checks. Usage: node prototypes/rose-keyboard/model-check.cjs
'use strict';
const K = require('./src/keyboard.js');
let pass = 0, fail = 0;
const check = (n, c, x) => { if (c) pass++; else fail++; console.log((c ? 'PASS ' : 'FAIL ') + n + (c ? '' : '  ' + JSON.stringify(x))); };
const typeStr = (s, str, t0) => { let t = t0 || 0; for (const ch of str) { t += 1000; if (ch === ' ') K.space(s, t); else K.type(s, ch); } return t; };

let s = K.initial();
check('starts with auto-capitalised shift', s.shift === 'once');
typeStr(s, 'hello');
check('first letter capitalised, shift releases', s.text === 'Hello' && s.shift === 'off', s.text);
K.space(s, 10); K.space(s, 200);
check('double space ends a sentence', s.text === 'Hello. ' && s.shift === 'once', s.text);
typeStr(s, 'teh ', 1e6);
check('autocorrect on space (case kept)', s.text === 'Hello. The ', s.text);
K.backspace(s);
check('backspace right after autocorrect restores what was typed', s.text === 'Hello. Teh', s.text);
check('reverted word is not re-suggested as a correction', !K.suggestions(s).some((x) => x.kind === 'correct'), K.suggestions(s));
K.deleteWord(s);
check('delete word', s.text === 'Hello. ', s.text);

s = K.initial(); K.shiftTap(s, 0); // once -> off
check('tapping auto-shift turns it off', s.shift === 'off');
K.shiftTap(s, 1000); K.shiftTap(s, 1200);
check('double-tap shift locks caps', s.shift === 'lock');
typeStr(s, 'ok');
check('caps lock stays on', s.text === 'OK' && s.shift === 'lock', s.text);
K.shiftTap(s, 5000);
check('tap shift releases caps lock', s.shift === 'off');

s = K.initial(); typeStr(s, 'see you at th');
let sg = K.suggestions(s);
check('three suggestions, typed word first, best in the middle', sg.length === 3 && sg[0].text === 'th' && sg[1].text === 'the', sg);
K.pick(s, sg[1].text);
check('picking a suggestion replaces the word and adds a space', s.text === 'See you at the ', s.text);
sg = K.suggestions(s);
check('next-word predictions between words', sg.length === 3 && sg[0].kind === 'predict' && sg.map((x) => x.text).includes('garden'), sg);
s = K.initial(); typeStr(s, 'dont');
sg = K.suggestions(s);
check('correction offered in the middle slot', sg[1] && sg[1].kind === 'correct' && sg[1].text === "Don't", sg);

s = K.initial({ text: 'Garden at ten' });
K.selectWord(s, 8);
check('select word', s.text.slice(Math.min(s.a, s.b), Math.max(s.a, s.b)) === 'at');
K.cut(s); check('cut', s.text === 'Garden  ten' && s.clip === 'at', s.text);
K.move(s, 1); K.paste(s); check('paste at caret', s.text === 'Garden  atten', s.text);
K.undo(s); check('undo paste', s.text === 'Garden  ten', s.text);
K.undo(s); check('undo cut', s.text === 'Garden at ten', s.text);
K.redo(s); check('redo', s.text === 'Garden  ten', s.text);
K.selectAll(s); K.type(s, 'x'); check('typing replaces selection', s.text.toLowerCase() === 'x', s.text);

s = K.initial(); typeStr(s, 'abc'); K.undo(s); check('typing a word undoes as one step', s.text === '', s.text);
s = K.initial(); typeStr(s, 'hi there'); const sent = K.enter(s);
check('single-line Return sends and clears', sent === 'Hi there' && s.text === '', s.text);
s = K.initial({ multiline: true }); typeStr(s, 'list'); K.enter(s); typeStr(s, 'milk');
check('multi-line Return inserts a newline and capitalises the next line', s.text === 'List\nMilk', s.text);
s = K.initial(); K.setPage(s, 'sym1'); K.type(s, '1'); K.setPage(s, 'sym2'); K.type(s, '€'); K.setPage(s, 'letters');
check('symbol pages type and return to letters', s.text === '1€' && s.page === 'letters', s.text);
check('layouts: 4 rows each, 10 keys on top', ['letters', 'sym1', 'sym2'].every((p) => K.LAYOUTS[p].length === 4 && K.LAYOUTS[p][0].length === 10));
s = K.initial({ text: 'ab' }); K.setSel(s, 1); K.move(s, 1, true); check('extend selection', s.a === 1 && s.b === 2);
s = K.initial({ text: 'hi 👋' }); K.backspace(s); check('backspace removes a whole emoji', s.text === 'hi ', s.text);
s = K.initial(); typeStr(s, 'i dont know'); K.type(s, '.');
check('corrections also apply before punctuation', s.text === "I don't know.", s.text);
s = K.initial({ multiline: true }); typeStr(s, 'call jamie'); K.enter(s);
check('corrections also apply on Return', s.text === 'Call Jamie\n', s.text);
console.log(pass + ' model checks passed' + (fail ? ', ' + fail + ' FAILED' : ''));
if (fail) process.exitCode = 1;
