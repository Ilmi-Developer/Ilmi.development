/* Checks the Reports tab's grade calculations (src/page.html, between REPORT-CALC START and REPORT-CALC END)
   against the rules and worked examples of the school's 2026-27 report spreadsheet.

   Usage: node tools/report_check.js  (exits 1 on the first failure) */
"use strict";
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const vm = require("vm");

const page = fs.readFileSync(path.join(__dirname, "..", "src", "page.html"), "utf8");
const start = page.indexOf("REPORT-CALC START");
const end = page.indexOf("/* REPORT-CALC END */");
assert(start > 0 && end > start, "REPORT-CALC markers not found in src/page.html");
const code = page.slice(page.indexOf("*/", start) + 2, end);
const box = {plural: (n, one, many) => n + " " + (n === 1 ? one : (many || one + "s"))};
vm.createContext(box);
vm.runInContext(code + ";this.F={rpPct,rpPerf,rpWarn,rpMemo,rpFinal,rpCalc,rpNote,RPD};", box);
const {rpPct, rpPerf, rpWarn, rpMemo, rpCalc, rpNote, RPD} = box.F;
const C = RPD, T1 = RPD.terms[0], FOCUS = ["Pronunciation", "Harakah", "Madd"];
const day = t => new Date(t).toISOString().slice(0, 10);
const at = d => Date.parse(d + "T12:00:00Z");
// Positions of targets with the workbook's level lengths, for the worked examples (Ilmi uses its own LEVELS).
const LEVELS = [9, 9, 9, 9, 10, 8, 10, 12, 15, 14];
const pos = (l, t) => LEVELS.slice(0, l - 1).reduce((a, b) => a + b, 0) + t;
const label = n => { let l = 0; while (l < LEVELS.length - 1 && n > LEVELS[l]) n -= LEVELS[l++]; return "L" + (l + 1) + " T" + n; };
const LAST = pos(10, 14);
const pupil = (o = {}) => Object.assign({name: "P", q: {}, log: [], lt: [], lx: [], tests: []}, o);
let n = 0;
// Values made inside the sandbox have its own Object and Array, so compare them as JSON.
const same = (actual, expected, msg) => assert.deepStrictEqual(JSON.parse(JSON.stringify(actual)), expected, msg);
const check = (name, fn) => { fn(); n++; };

check("percentage scale", () => {
  same([0, 29.9, 30, 49, 50, 69.9, 70, 89, 90, 100].map(p => rpPct(p, C)), [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]);
  assert.strictEqual(rpPct(null, C), null);
});
check("performance scale", () => {
  same([0, 0.69, 0.7, 0.89, 0.9, 1.09, 1.1, 1.29, 1.3, 2].map(r => rpPerf(r * 10, 10, C)), [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]);
  assert.strictEqual(rpPerf(0, 0, C), 3, "a zero class mean gives grade 3");
});
check("warnings scale", () => {
  same([0.69, 0.7, 0.9, 1.1, 1.3, 3].map(r => rpWarn(r * 10, 10, C)), [5, 4, 3, 2, 1, 1]);
  assert.strictEqual(rpWarn(0, 2, C), 5, "no warnings is grade 5");
});
check("Level 1 Target 9 passed: one new pass, now on L2 T1", () => {
  const s = pupil({log: [at("2026-06-01"), at("2026-10-03")], lt: [pos(1, 8), pos(1, 9)], lx: ["", ""]});
  const m = rpMemo(s, T1, day);
  same([m.passed, label(m.end + 1)], [1, "L2 T1"]);
  assert.strictEqual(rpNote(m, LAST, label), "1 target passed · on L2 T1");
});
check("a repeat pass adds nothing, a jump counts every target", () => {
  const rep = pupil({log: [at("2026-06-01"), at("2026-10-03")], lt: [pos(1, 9), pos(1, 9)], lx: ["", ""]});
  assert.strictEqual(rpMemo(rep, T1, day).passed, 0);
  const jump = pupil({log: [at("2026-06-01"), at("2026-10-03")], lt: [pos(1, 5), pos(1, 9)], lx: ["", ""]});
  assert.strictEqual(rpMemo(jump, T1, day).passed, 4);
  const surah = pupil({log: [at("2026-10-03")], lt: [pos(1, 9)], lx: ["212. Surah Al-Mulk"]});
  assert.strictEqual(rpMemo(surah, T1, day).passed, 0, "a surah pass isn't a target position");
});
check("term dates include both boundary days", () => {
  const s = pupil({log: [at("2026-09-12"), at("2026-12-20"), at("2026-12-21")], lt: [1, 2, 9], lx: ["", "", ""]});
  assert.strictEqual(rpMemo(s, T1, day).passed, 2);
});
check("Complete once the last target is passed", () => {
  assert.strictEqual(rpNote({passed: 3, end: LAST}, LAST, label), "3 targets passed · Complete");
});
check("Progress: 6 of 8 expected is 75%, grade 4", () => {
  const log = [1, 2, 3, 4, 5, 6].map(k => at("2026-10-0" + k));
  const R = rpCalc([pupil({log, lt: [1, 2, 3, 4, 5, 6], lx: ["", "", "", "", "", ""]})], T1, C, FOCUS, day);
  assert.strictEqual(R[0].m.c.pg, 4);
});
check("Homework: the rounded mean score of the term's passed tests, per subject; blank with none", () => {
  const tests = [{d: "2026-10-03", k: "q", n: 3, sc: 4, ok: true}, {d: "2026-10-10", k: "q", n: 4, sc: 3, ok: true},
                 {d: "2026-10-17", k: "q", n: 5, sc: 1, ok: false}, {d: "2026-10-24", k: "m", n: 9, sc: 2, ok: true},
                 {d: "2026-11-07", k: "m", n: 10, sc: 4, ok: true, x: "212. Surah Al-Mulk"}, {d: "2027-01-16", k: "m", n: 11, sc: 1, ok: true}];
  const R = rpCalc([pupil({tests, hw: {"2026-10-03": 0}}), pupil({q: {"2026-10-03": 4}, tests: [{d: "2026-10-03", k: "q", n: 1, sc: 2, ok: false}]})],
                   T1, C, FOCUS, day);
  same([R[0].q.c.hw, R[0].m.c.hw], [4, 3], "Quran (4 + 3) / 2 = 3.5 rounds to 4, the fail left out; Memo (2 + 4) / 2 = 3, Term 2 left out");
  same([R[1].q.c.hw, R[1].m.c.hw], [null, null], "no passes, no grade");
});
check("Pronunciation and Fluency from the term's Quran level tests; focus ties go by the settings order", () => {
  const tests = [{d: "2026-10-03", k: "q", n: 3, sc: 4, ok: true, fa: ["Madd", "Harakah"]},
                 {d: "2026-11-07", k: "q", n: 4, sc: 3, ok: true, fa: ["Madd", "Harakah"]},
                 {d: "2026-11-08", k: "m", n: 9, sc: 1, ok: false, fa: ["Pronunciation"]},
                 {d: "2027-01-12", k: "q", n: 5, sc: 1, ok: false}];
  const R = rpCalc([pupil({tests})], T1, C, FOCUS, day);
  same([R[0].q.c.pr, R[0].q.c.fl], [4, 4], "(4 + 3) / 2 = 3.5 rounds to 4; Memo and Term 2 tests left out");
  same(R[0].focus, {f: "Harakah", n: 2});
});
check("active group, class-relative grades and inactive pupils", () => {
  const a = pupil({name: "A", q: {"2026-10-03": 5}, wl: [{t: at("2026-10-03"), v: 1}, {t: at("2026-10-10"), v: 2}],
    log: [at("2026-10-03"), at("2026-10-10")], lt: [1, 2], lx: ["", ""], mt: {"2026-10-03": "t", "2026-10-10": "t"}});
  const b = pupil({name: "B", q: {"2026-10-03": 3}, mt: {"2026-10-03": "t"}});
  const off = pupil({name: "C", q: {"2026-10-03": -1}, wl: [{t: at("2026-10-03"), v: 3}]});
  const R = rpCalc([a, b, off], T1, C, FOCUS, day);
  same(R.map(r => r.act), [true, true, false], "absent only is not a lesson");
  // Warnings: A 2, B 0, mean 1. A's ratio 2 is grade 1; B has none, grade 5.
  same([R[0].q.c.mb, R[1].q.c.mb, R[2].q.c.mb], [1, 5, null]);
  // Points: A 2 tests + 2 new targets x 5 = 12, B 1 test = 1, mean 6.5.
  same([R[0].m.c.cp, R[1].m.c.cp], [5, 1]);
  assert.strictEqual(R[2].m.f.ef, null);
});
check("Effort: rounded mean of the final grades, teacher grades included; an Effort override wins", () => {
  const s = pupil({tests: [{d: "2026-10-03", k: "q", n: 1, sc: 2, ok: true}], ro: {"t1-2026": {q: {hw: 5}}}});
  const R = rpCalc([s], T1, C, FOCUS, day);
  // Manners 5 (no warnings), Homework 5 (teacher, worked out 2), Pronunciation 2, Fluency 2: (5 + 5 + 2 + 2) / 4 = 3.5, Effort 4.
  same([R[0].q.c.hw, R[0].q.f.hw, R[0].q.f.ef], [2, 5, 4]);
  s.ro["t1-2026"].q.ef = 2;
  assert.strictEqual(rpCalc([s], T1, C, FOCUS, day)[0].q.f.ef, 2);
  s.ro["t1-2026"].q = {mb: 1};
  assert.strictEqual(rpCalc([s], T1, C, FOCUS, day)[0].q.f.ef, 2, "(1 + 2 + 2 + 2) / 4 = 1.75 rounds to 2");
});
check("Journey 2 Jannah attendance for the term", () => {
  const R = rpCalc([pupil({ja: {"2026-10-03": 1, "2026-10-04": 0, "2026-10-10": 1, "2027-01-16": 0}})], T1, C, FOCUS, day);
  same(R[0].att, {p: 2, n: 3, pct: 67});
});
console.log("Report calculations: " + n + " checks passed.");
