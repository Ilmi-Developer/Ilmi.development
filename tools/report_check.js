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
vm.runInContext(code + ";this.F={repPct,repPerf,repWarn,repMemo,repFinal,repCalc,repNote,REPD};", box);
const {repPct, repPerf, repWarn, repMemo, repCalc, repNote, REPD} = box.F;
const C = REPD, T1 = REPD.terms[0], FOCUS = ["Pronunciation", "Harakah", "Madd"];
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
  same([0, 29.9, 30, 49, 50, 69.9, 70, 89, 90, 100].map(p => repPct(p, C)), [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]);
  assert.strictEqual(repPct(null, C), null);
});
check("performance scale", () => {
  same([0, 0.69, 0.7, 0.89, 0.9, 1.09, 1.1, 1.29, 1.3, 2].map(r => repPerf(r * 10, 10, C)), [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]);
  assert.strictEqual(repPerf(0, 0, C), 3, "a zero class mean gives grade 3");
});
check("warnings scale", () => {
  same([0.69, 0.7, 0.9, 1.1, 1.3, 3].map(r => repWarn(r * 10, 10, C)), [5, 4, 3, 2, 1, 1]);
  assert.strictEqual(repWarn(0, 2, C), 5, "no warnings is grade 5");
});
check("Level 1 Target 9 passed: one new pass, now on L2 T1", () => {
  const s = pupil({log: [at("2026-06-01"), at("2026-10-03")], lt: [pos(1, 8), pos(1, 9)], lx: ["", ""]});
  const m = repMemo(s, T1, day);
  same([m.passed, label(m.end + 1)], [1, "L2 T1"]);
  assert.strictEqual(repNote(m, LAST, label), "1 target passed · on L2 T1");
});
check("a repeat pass adds nothing, a jump counts every target", () => {
  const rep = pupil({log: [at("2026-06-01"), at("2026-10-03")], lt: [pos(1, 9), pos(1, 9)], lx: ["", ""]});
  assert.strictEqual(repMemo(rep, T1, day).passed, 0);
  const jump = pupil({log: [at("2026-06-01"), at("2026-10-03")], lt: [pos(1, 5), pos(1, 9)], lx: ["", ""]});
  assert.strictEqual(repMemo(jump, T1, day).passed, 4);
  const surah = pupil({log: [at("2026-10-03")], lt: [pos(1, 9)], lx: ["212. Surah Al-Mulk"]});
  assert.strictEqual(repMemo(surah, T1, day).passed, 0, "a surah pass isn't a target position");
});
check("term dates include both boundary days", () => {
  const s = pupil({log: [at("2026-09-12"), at("2026-12-20"), at("2026-12-21")], lt: [1, 2, 9], lx: ["", "", ""]});
  assert.strictEqual(repMemo(s, T1, day).passed, 2);
});
check("Complete once the last target is passed", () => {
  assert.strictEqual(repNote({passed: 3, end: LAST}, LAST, label), "3 targets passed · Complete");
});
check("Progress: 6 of 8 expected is 75%, grade 4", () => {
  const log = [1, 2, 3, 4, 5, 6].map(k => at("2026-10-0" + k));
  const R = repCalc([pupil({log, lt: [1, 2, 3, 4, 5, 6], lx: ["", "", "", "", "", ""]})], T1, C, FOCUS, day);
  assert.strictEqual(R[0].m.c.pg, 4);
});
check("Quran Homework: the rounded mean score of the term's passed Quran tests; blank with none", () => {
  const tests = [{d: "2026-10-03", k: "q", n: 3, sc: 4, ok: true}, {d: "2026-10-10", k: "q", n: 4, sc: 3, ok: true},
                 {d: "2026-10-17", k: "q", n: 5, sc: 1, ok: false}, {d: "2026-10-24", k: "m", n: 9, sc: 2, ok: true},
                 {d: "2026-11-07", k: "m", n: 10, sc: 4, ok: true, x: "212. Surah Al-Mulk"}, {d: "2027-01-16", k: "m", n: 11, sc: 1, ok: true}];
  const R = repCalc([pupil({tests, hw: {"2026-10-03": 0}}), pupil({q: {"2026-10-03": 4}, tests: [{d: "2026-10-03", k: "q", n: 1, sc: 2, ok: false}]})],
                   T1, C, FOCUS, day);
  same(R[0].q.c.hw, 4, "(4 + 3) / 2 = 3.5 rounds to 4; the fail and Memo tests left out");
  same(R[1].q.c.hw, null, "no passes, no grade");
});
check("Pronunciation and Fluency from the term's Quran level tests; focus ties go by the settings order", () => {
  const tests = [{d: "2026-10-03", k: "q", n: 3, sc: 4, ok: true, fa: ["Madd", "Harakah"]},
                 {d: "2026-11-07", k: "q", n: 4, sc: 3, ok: true, fa: ["Madd", "Harakah"]},
                 {d: "2026-11-08", k: "m", n: 9, sc: 1, ok: false, fa: ["Pronunciation"]},
                 {d: "2027-01-12", k: "q", n: 5, sc: 1, ok: false}];
  const R = repCalc([pupil({tests})], T1, C, FOCUS, day);
  same([R[0].q.c.pr, R[0].q.c.fl], [4, 4], "(4 + 3) / 2 = 3.5 rounds to 4; Memo and Term 2 tests left out");
  same(R[0].focus, {f: "Harakah", n: 2});
});
check("active group, class-relative grades and inactive pupils", () => {
  const a = pupil({name: "A", q: {"2026-10-03": 5}, wl: [{t: at("2026-10-03"), v: 1}, {t: at("2026-10-10"), v: 2}],
    log: [at("2026-10-03"), at("2026-10-10")], lt: [1, 2], lx: ["", ""], mt: {"2026-10-03": "t", "2026-10-10": "t"}});
  const b = pupil({name: "B", q: {"2026-10-03": 3}, mt: {"2026-10-03": "t"}});
  const off = pupil({name: "C", q: {"2026-10-03": -1}, wl: [{t: at("2026-10-03"), v: 3}]});
  const R = repCalc([a, b, off], T1, C, FOCUS, day);
  same(R.map(r => r.act), [true, true, false], "absent only is not a lesson");
  // Warnings: A 2, B 0, mean 1. A's ratio 2 is grade 1 on the scale, raised to the lowest, 2; B has none, grade 5.
  same([R[0].q.c.mb, R[1].q.c.mb, R[2].q.c.mb], [2, 5, null]);
  // Memo Homework: new targets A 2, B 0, mean 1, so ratios 2 and 0.
  same([R[0].m.c.hw, R[1].m.c.hw], [5, 2]);
  // Points: A 2 tests + 2 new targets x 5 = 12, B 1 test = 1, mean 6.5.
  same([R[0].m.c.cp, R[1].m.c.cp], [5, 2]);
  assert.strictEqual(R[2].m.f.ef, null);
});
check("Effort: rounded mean of the final grades, teacher grades included; an Effort override wins", () => {
  const s = pupil({tests: [{d: "2026-10-03", k: "q", n: 1, sc: 2, ok: true}], ro: {"t1-2026": {q: {hw: 5}}}});
  const R = repCalc([s], T1, C, FOCUS, day);
  // Manners 5 (no warnings), Homework 5 (teacher, worked out 2), Pronunciation 2, Fluency 2: (5 + 5 + 2 + 2) / 4 = 3.5, Effort 4.
  same([R[0].q.c.hw, R[0].q.f.hw, R[0].q.f.ef], [2, 5, 4]);
  s.ro["t1-2026"].q.ef = 2;
  assert.strictEqual(repCalc([s], T1, C, FOCUS, day)[0].q.f.ef, 2);
  s.ro["t1-2026"].q = {mb: 1};
  assert.strictEqual(repCalc([s], T1, C, FOCUS, day)[0].q.f.ef, 2, "(1 + 2 + 2 + 2) / 4 = 1.75 rounds to 2");
});
check("worked-out grades never go below 2, but a teacher can give a 1", () => {
  const s = pupil({q: {"2026-10-03": 4}, tests: [{d: "2026-10-03", k: "q", n: 1, sc: 1, ok: true}, {d: "2026-10-10", k: "q", n: 2, sc: 0, ok: false}],
    ro: {"t1-2026": {q: {fl: 1}}}});
  const R = repCalc([s], T1, C, FOCUS, day);
  // Homework 1 (the pass) and Pronunciation 1 ((1 + 0) / 2 = 0.5 rounds to 1) are raised to 2; Fluency is the teacher's 1.
  same([R[0].q.c.hw, R[0].q.c.pr, R[0].q.f.fl], [2, 2, 1]);
  // Effort: (5 + 2 + 2 + 1) / 4 = 2.5 rounds to 3.
  assert.strictEqual(R[0].q.f.ef, 3);
  same(repCalc([s], T1, Object.assign({}, C, {lowest: 1}), FOCUS, day)[0].q.c.hw, 1, "the lowest is a setting");
});
check("Journey 2 Jannah attendance for the term", () => {
  const R = repCalc([pupil({ja: {"2026-10-03": 1, "2026-10-04": 0, "2026-10-10": 1, "2027-01-16": 0}})], T1, C, FOCUS, day);
  same(R[0].att, {p: 2, n: 3, pct: 67});
});
console.log("Report calculations: " + n + " checks passed.");
