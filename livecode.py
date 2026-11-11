#!/usr/bin/env python3
"""LiveCode v2 — lokal kompilyator (C / C++ / Python).
Ishga tushirish:  python3 livecode.py
Talab: clang, clang++ (xcode-select --install) va python3.
Faqat O'Z kompyuteringizda, o'z laboratoriyangizda ishlating."""
import http.server, json, os, secrets, shlex, shutil, signal, subprocess, sys, time
import tempfile, resource, webbrowser
from urllib.parse import urlparse, parse_qs

HOST = "127.0.0.1"                      # faqat lokal
PORT = int(os.environ.get("LIVECODE_PORT", "8765"))
TOKEN = secrets.token_hex(16)           # har safar yangi token
MAX_CODE, MAX_STDIN, MAX_OUT = 100_000, 10_000, 100_000
COMPILE_T = 20

# Foydalanuvchidan keladigan sozlamalar FAQAT shu ro'yxatdan tanlanadi (whitelist)
CC = {"c": "clang", "cpp": "clang++"}
FILES = {"c": "main.c", "cpp": "main.cpp", "python": "main.py"}
STD = {"c": ("c99", "c11", "c17"), "cpp": ("c++14", "c++17", "c++20")}
DEF_STD = {"c": "c17", "cpp": "c++17"}
OPTS = ("-O0", "-O1", "-O2")
TIMES = (2, 5, 10)

def run_limits(sec):
    def f():                            # dastur ishlaganda: CPU, fayl hajmi, core dump
        resource.setrlimit(resource.RLIMIT_CPU, (sec + 1, sec + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1_000_000, 1_000_000))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return f

def run_proc(cmd, cwd, stdin_data, timeout, limited):
    # Minimal muhit: API kalit va sirlar foydalanuvchi kodiga o'tmaydi
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/opt/homebrew/bin"),
           "HOME": cwd, "TMPDIR": cwd, "LANG": "en_US.UTF-8"}
    with tempfile.TemporaryFile() as fin, tempfile.TemporaryFile() as fout:
        fin.write(stdin_data.encode("utf-8")); fin.seek(0)
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=fin, stdout=fout,
                             stderr=subprocess.STDOUT, shell=False,
                             preexec_fn=run_limits(timeout) if limited else None,
                             start_new_session=True)
        timed_out = False
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try: os.killpg(p.pid, signal.SIGKILL)   # butun jarayon guruhini o'ldirish
            except ProcessLookupError: pass
            p.wait()
        fout.seek(0)
        raw = fout.read(MAX_OUT + 1)
    return p.returncode, raw[:MAX_OUT].decode("utf-8", "replace"), timed_out, len(raw) > MAX_OUT

def parse_args(s):
    # Buyruq qatori argumentlari: shell ishlatilmaydi, soni va uzunligi cheklangan
    if not isinstance(s, str) or len(s) > 500:
        return []
    try:
        parts = shlex.split(s)
    except ValueError:
        return []
    return [x for x in parts[:10] if len(x) <= 100 and "\0" not in x]

def execute(lang, code, stdin, opts):
    t = opts.get("time"); t = t if t in TIMES else 5
    res = {"compile": "", "compile_failed": False, "output": "", "code": None,
           "timeout": False, "truncated": False, "ms_compile": 0, "ms_run": 0}
    with tempfile.TemporaryDirectory(prefix="livecode_") as d:   # vaqtinchalik papka
        with open(os.path.join(d, FILES[lang]), "w", encoding="utf-8") as f:
            f.write(code)
        if lang in CC:
            cc = CC[lang]
            if not shutil.which(cc):
                return {"error": cc + " topilmadi. Terminalda: xcode-select --install"}
            std = opts.get("std"); std = std if std in STD[lang] else DEF_STD[lang]
            opt = opts.get("opt"); opt = opt if opt in OPTS else "-O0"
            cmd = [cc, "-std=" + std, opt, "-Wall", "-Wextra"]
            if opts.get("san") is True:                           # xotira xatolarini topadi
                cmd += ["-g", "-fsanitize=address,undefined"]
            if opts.get("asm") is True:                           # assembly ko'rinishi
                cmd = [c for c in cmd if c != "-g" and not c.startswith("-fsanitize")]
                cmd += ["-S", "-o", "main.s", FILES[lang]]
                rc, out, to, _ = run_proc(cmd, d, "", COMPILE_T, False)
                res["compile"] = out
                if to or rc != 0:
                    res.update(code=rc, compile_failed=True)
                    return res
                with open(os.path.join(d, "main.s"), "r", encoding="utf-8", errors="replace") as f:
                    res.update(code=0, asm=f.read(MAX_OUT))
                return res
            cmd += ["-o", "main", FILES[lang]] + (["-lm"] if lang == "c" else [])
            t0 = time.monotonic()
            rc, out, to, _ = run_proc(cmd, d, "", COMPILE_T, False)
            res["ms_compile"] = int((time.monotonic() - t0) * 1000)
            res["compile"] = out
            if to:
                return {"error": "Kompilyatsiya vaqti tugadi."}
            if rc != 0:
                res.update(code=rc, compile_failed=True)
                return res
            run_cmd = ["./main"]
        else:
            run_cmd = [sys.executable, "-I", FILES[lang]]         # -I: izolyatsiya
        run_cmd += parse_args(opts.get("args", ""))
        t0 = time.monotonic()
        rc, out, to, trunc = run_proc(run_cmd, d, stdin, t, True)
        res.update(output=out, code=rc, timeout=to, truncated=trunc,
                   ms_run=int((time.monotonic() - t0) * 1000))
        return res

PAGE = r"""<!DOCTYPE html>
<html lang="uz"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LiveCode — C / C++ / Python</title>
<style>
:root{--bg:#f4f5f7;--panel:#fff;--bar:#eceef2;--text:#1d2330;--muted:#667085;--line:#d5d9e2;--accent:#3b6cf6;--ok:#1b7f3b;--bad:#c62828;--warn:#9a6200;
--tk-com:#7a869a;--tk-str:#0a7d4b;--tk-num:#b4540a;--tk-k:#8a2be2;--tk-t:#0b6bcb;--tk-f:#a1281e;--tk-pre:#b4540a;--fs:14px;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--font:system-ui,-apple-system,"Segoe UI",sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#12151c;--panel:#1a1f2b;--bar:#222836;--text:#e6e9f0;--muted:#97a0b3;--line:#2f3648;--accent:#6c93ff;--ok:#5fd68a;--bad:#ff7b7b;--warn:#f2b45a;
--tk-com:#7d889c;--tk-str:#8fd694;--tk-num:#f2a766;--tk-k:#c79bff;--tk-t:#7fb5ff;--tk-f:#ff9e8f;--tk-pre:#f2a766}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#12151c;--panel:#1a1f2b;--bar:#222836;--text:#e6e9f0;--muted:#97a0b3;--line:#2f3648;--accent:#6c93ff;--ok:#5fd68a;--bad:#ff7b7b;--warn:#f2b45a;
--tk-com:#7d889c;--tk-str:#8fd694;--tk-num:#f2a766;--tk-k:#c79bff;--tk-t:#7fb5ff;--tk-f:#ff9e8f;--tk-pre:#f2a766}
*,*::before,*::after{box-sizing:border-box}
html,body{height:100%}
body{margin:0;display:flex;flex-direction:column;background:var(--bg);color:var(--text);font-family:var(--font)}
header{display:flex;flex-wrap:wrap;gap:.5rem .9rem;align-items:center;padding:.5rem .8rem;background:var(--panel);border-bottom:1px solid var(--line)}
h1{font-size:1.05rem;margin:0}h1 span{color:var(--accent)}
.tools{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center}
select,button,.btn{font:inherit;font-size:.85rem;padding:.35rem .65rem;border:1px solid var(--line);border-radius:7px;background:var(--bar);color:var(--text);cursor:pointer}
button:hover,.btn:hover{border-color:var(--accent)}
button.run{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
button:disabled{opacity:.6;cursor:wait}
label.chk{font-size:.85rem;color:var(--muted);display:flex;gap:.3rem;align-items:center}
[hidden]{display:none!important}
input#args{font:inherit;font-size:.85rem;padding:.35rem .5rem;border:1px solid var(--line);border-radius:7px;background:var(--bar);color:var(--text);width:9rem}
main{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr)}
.left{display:flex;flex-direction:column;min-height:0;border-right:1px solid var(--line)}
.editor{flex:1;min-height:0;display:flex;background:var(--panel)}
.gutter{margin:0;padding:10px 8px 40px;min-width:3.2em;text-align:right;color:var(--muted);background:var(--bar);overflow:hidden;user-select:none;font-family:var(--mono);font-size:var(--fs);line-height:1.6}
.codewrap{position:relative;flex:1;min-width:0}
.codewrap pre,.codewrap textarea{position:absolute;inset:0;margin:0;padding:10px 12px 40px;border:0;font-family:var(--mono);font-size:var(--fs);line-height:1.6;tab-size:4;white-space:pre;overflow-wrap:normal}
.codewrap pre{overflow:hidden;pointer-events:none;color:var(--text)}
.codewrap code{font:inherit}
.codewrap textarea{overflow:auto;resize:none;outline:0;background:transparent;color:transparent;caret-color:var(--text)}
.tk-com{color:var(--tk-com);font-style:italic}.tk-str{color:var(--tk-str)}.tk-num{color:var(--tk-num)}
.tk-k{color:var(--tk-k);font-weight:600}.tk-t{color:var(--tk-t)}.tk-f{color:var(--tk-f)}.tk-pre{color:var(--tk-pre)}
.status{padding:.25rem .8rem;font-size:.75rem;color:var(--muted);background:var(--bar);border-top:1px solid var(--line);display:flex;justify-content:space-between}
.right{display:flex;flex-direction:column;min-height:0;background:var(--panel)}
.lbl{padding:.3rem .8rem;font-size:.72rem;font-weight:700;letter-spacing:.06em;color:var(--muted);background:var(--bar);display:flex;justify-content:space-between;align-items:center}
.lbl button{padding:.1rem .5rem;font-size:.72rem}
#stdin{width:100%;height:5.5rem;resize:vertical;border:0;border-bottom:1px solid var(--line);padding:.6rem .8rem;background:var(--panel);color:var(--text);font-family:var(--mono);font-size:.85rem;outline:0}
#stdin.need{box-shadow:inset 3px 0 0 var(--warn)}
#out{flex:1;min-height:0;overflow:auto}
#out pre{margin:0;padding:.5rem .8rem;white-space:pre-wrap;word-break:break-word;font-family:var(--mono);font-size:.85rem}
#out .sec{padding:.2rem .8rem;font-size:.7rem;font-weight:700;color:var(--muted);border-top:1px solid var(--line)}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.meta{padding:.4rem .8rem;font-size:.78rem;color:var(--muted);border-top:1px solid var(--line)}
@media (max-width:820px){html,body{height:auto}main{display:flex;flex-direction:column}.editor{height:380px;flex:none}.right{min-height:380px}}
</style></head><body>
<header>
  <h1>Live<span>Code</span></h1>
  <div class="tools">
    <select id="lang" aria-label="Til"><option value="c">C</option><option value="cpp">C++</option><option value="python">Python</option></select>
    <select id="ex" aria-label="Namunalar"></select>
    <span id="copts" class="tools">
      <select id="std" aria-label="Standart"></select>
      <select id="opt" aria-label="Optimizatsiya"><option>-O0</option><option>-O1</option><option>-O2</option></select>
      <label class="chk"><input type="checkbox" id="san"> Xotira tekshiruvi (ASan)</label>
    </span>
    <select id="time" aria-label="Vaqt chegarasi"><option value="2">2 s</option><option value="5">5 s</option><option value="10">10 s</option></select>
    <select id="task" aria-label="Topshiriqlar"></select>
    <input id="args" maxlength="500" placeholder="argumentlar" aria-label="Buyruq qatori argumentlari" spellcheck="false">
    <button id="asm" type="button" title="Kompilyator hosil qilgan assembly kodni ko'rsatadi">⚙ Assembly</button>
    <button id="test" type="button" hidden>✔ Tekshirish</button>
    <button id="run" class="run" type="button">▶ Ishga tushirish</button>
    <button id="save" type="button">⬇ Saqlash</button>
    <label class="btn" for="file">📂 Ochish</label><input type="file" id="file" accept=".c,.h,.cpp,.cc,.hpp,.py,.txt" hidden>
    <button id="fm" type="button" title="Shriftni kichraytirish">A−</button>
    <button id="fp" type="button" title="Shriftni kattalashtirish">A+</button>
    <button id="theme" type="button">Mavzu</button>
  </div>
</header>
<main>
  <section class="left">
    <div class="editor">
      <pre class="gutter" id="gutter" aria-hidden="true">1</pre>
      <div class="codewrap">
        <pre id="hl" aria-hidden="true"><code id="hlc"></code></pre>
        <textarea id="code" wrap="off" maxlength="100000" spellcheck="false" autocapitalize="off" autocomplete="off" aria-label="Kod"></textarea>
      </div>
    </div>
    <div class="status"><span id="pos">Qator 1, Ustun 1</span><span>Cmd/Ctrl+Enter — ishga tushirish · Cmd/Ctrl+/ — izoh</span></div>
  </section>
  <section class="right">
    <div class="lbl">KIRISH (stdin)</div>
    <textarea id="stdin" maxlength="10000" spellcheck="false" aria-label="Kirish"></textarea>
    <div class="lbl">NATIJA <span><button id="copy" type="button">Nusxa</button> <button id="clear" type="button">Tozalash</button></span></div>
    <div id="out" aria-live="polite"></div>
    <div class="meta" id="meta">Tayyor.</div>
  </section>
</main>
<script>
"use strict";
const T = "__TOKEN__";
const KEY = "livecode:v2";
const MAXC = 100000;
const $ = (i) => document.getElementById(i);
const ed = $("code"), hlc = $("hlc"), hl = $("hl"), gut = $("gutter"), out = $("out");

/* ---------- Namunalar ---------- */
const EX = {
c: [
 ["Salom (stdin)", String.raw`#include <stdio.h>
#include <string.h>

int main(void) {
    char name[64];
    printf("Ismingiz? ");
    if (fgets(name, sizeof name, stdin) == NULL) {
        printf("\n(Kirish bo'sh: stdin maydoniga ism yozing)\n");
        return 0;
    }
    name[strcspn(name, "\n")] = '\0';
    printf("Salom, %s!\n", name);
    return 0;
}
`],
 ["Fibonachchi", String.raw`#include <stdio.h>

int main(void) {
    long long a = 0, b = 1;
    for (int i = 0; i < 15; i++) {
        printf("%lld ", a);
        long long t = a + b;
        a = b;
        b = t;
    }
    printf("\n");
    return 0;
}
`],
 ["Saralash (qsort)", String.raw`#include <stdio.h>
#include <stdlib.h>

static int cmp(const void *x, const void *y) {
    int a = *(const int *)x, b = *(const int *)y;
    return (a > b) - (a < b);
}

int main(void) {
    int arr[] = {42, 7, 19, 3, 88, 1};
    size_t n = sizeof arr / sizeof arr[0];
    qsort(arr, n, sizeof arr[0], cmp);
    for (size_t i = 0; i < n; i++) printf("%d ", arr[i]);
    printf("\n");
    return 0;
}
`]],
cpp: [
 ["Salom (stdin)", String.raw`#include <iostream>
#include <string>

int main() {
    std::string name;
    std::cout << "Ismingiz? ";
    if (!std::getline(std::cin, name)) {
        std::cout << "\n(Kirish bo'sh: stdin maydoniga ism yozing)\n";
        return 0;
    }
    std::cout << "Salom, " << name << "!\n";
}
`],
 ["Vektor va lambda", String.raw`#include <algorithm>
#include <iostream>
#include <vector>

int main() {
    std::vector<int> v{42, 7, 19, 3, 88, 1};
    std::sort(v.begin(), v.end(), [](int a, int b) { return a < b; });
    for (int x : v) std::cout << x << ' ';
    std::cout << '\n';
}
`],
 ["Aqlli ko'rsatkich", String.raw`#include <iostream>
#include <memory>
#include <string>

struct Talaba {
    std::string ism;
    int yosh;
};

int main() {
    auto t = std::make_unique<Talaba>(Talaba{"Ali", 20});
    std::cout << t->ism << " " << t->yosh << "\n";
}
`]],
python: [
 ["Salom (stdin)", String.raw`try:
    name = input("Ismingiz? ")
    print(f"Salom, {name}!")
except EOFError:
    print("\n(Kirish bo'sh: stdin maydoniga ism yozing)")
`],
 ["Fibonachchi", String.raw`def fib(n):
    a, b = 0, 1
    for _ in range(n):
        yield a
        a, b = b, a + b

print(list(fib(15)))
`],
 ["Lug'at va saralash", String.raw`narx = {"olma": 12000, "nok": 18000, "uzum": 25000}
for nom, p in sorted(narx.items(), key=lambda kv: kv[1], reverse=True):
    print(f"{nom:<6} {p:>8,} so'm")
print("Jami:", sum(narx.values()))
`]]
};

/* ---------- Holat (localStorage, xavfsiz o'qish) ---------- */
const LANGS = ["c", "cpp", "python"];
const STD = { c: ["c99", "c11", "c17"], cpp: ["c++14", "c++17", "c++20"] };
const DEFSTD = { c: "c17", cpp: "c++17" };
function defaults() {
  return { lang: "c", codes: { c: EX.c[0][1], cpp: EX.cpp[0][1], python: EX.python[0][1] },
           stdin: "", std: { ...DEFSTD }, opt: "-O0", san: false, time: 5, theme: "auto", fs: 14 };
}
function load() {
  const S = defaults();
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return S;
    const d = JSON.parse(raw);
    if (LANGS.includes(d.lang)) S.lang = d.lang;
    for (const l of LANGS) if (d.codes && typeof d.codes[l] === "string" && d.codes[l].length <= MAXC) S.codes[l] = d.codes[l];
    if (typeof d.stdin === "string" && d.stdin.length <= 10000) S.stdin = d.stdin;
    for (const l of ["c", "cpp"]) if (d.std && STD[l].includes(d.std[l])) S.std[l] = d.std[l];
    if (["-O0", "-O1", "-O2"].includes(d.opt)) S.opt = d.opt;
    S.san = d.san === true;
    if ([2, 5, 10].includes(d.time)) S.time = d.time;
    if (["auto", "light", "dark"].includes(d.theme)) S.theme = d.theme;
    if (Number.isInteger(d.fs) && d.fs >= 11 && d.fs <= 24) S.fs = d.fs;
  } catch (e) { /* buzilgan ma'lumot — standart holat */ }
  return S;
}
const S = load();
function save() { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) {} }

/* ---------- Sintaksis ranglari (DOM orqali, innerHTML yo'q) ---------- */
const W = (s) => new Set(s.split(" "));
const KW = {
  c: W("auto break case const continue default do else enum extern for goto if inline register restrict return sizeof static struct switch typedef union volatile while"),
  cpp: W("auto break case catch class const constexpr continue default delete do else enum explicit extern final for friend goto if inline namespace new noexcept nullptr operator override private protected public return sizeof static struct switch template this throw try typedef typename union using virtual volatile while"),
  python: W("and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield True False None")
};
const TY = {
  c: W("char double float int long short signed unsigned void bool size_t FILE NULL true false int8_t int16_t int32_t int64_t uint8_t uint16_t uint32_t uint64_t"),
  cpp: W("char double float int long short signed unsigned void bool size_t string vector map set unique_ptr shared_ptr cout cin cerr endl std true false"),
  python: W("print input len range int str float list dict set tuple sum min max sorted enumerate zip open abs map filter isinstance type self")
};
const NUM = /\b(?:0[xX][\da-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?)[uUlLfF]*\b/;
const ID = /[A-Za-z_]\w*/;
const NOEND = "(?![\\s\\S])";   // matn oxiri (m bayrog'i bilan $ ishlatmaslik uchun)
const RULES = {
  c: [["com", new RegExp("\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?(?:\\*\\/|" + NOEND + ")")],
      ["str", /"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?/],
      ["pre", /^[ \t]*#[ \t]*\w+/m], ["num", NUM], ["id", ID]],
  python: [["com", /#[^\n]*/],
      ["str", new RegExp("[rbfRBF]{0,2}(?:\"{3}[\\s\\S]*?(?:\"{3}|" + NOEND + ")|'{3}[\\s\\S]*?(?:'{3}|" + NOEND + ")|\"(?:\\\\.|[^\"\\\\\\n])*\"?|'(?:\\\\.|[^'\\\\\\n])*'?)")],
      ["num", NUM], ["id", ID]]
};
const REGEX = {};
for (const k of ["c", "python"]) REGEX[k] = new RegExp(RULES[k].map(([, r]) => "(" + r.source + ")").join("|"), "gm");

function highlight() {
  const lang = S.lang, text = ed.value;
  const rk = lang === "python" ? "python" : "c";
  const re = REGEX[rk], kinds = RULES[rk].map((r) => r[0]);
  const frag = document.createDocumentFragment();
  let last = 0, m;
  re.lastIndex = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) frag.append(text.slice(last, m.index));
    let cls = null;
    for (let i = 0; i < kinds.length; i++) if (m[i + 1] !== undefined) { cls = kinds[i]; break; }
    const tok = m[0];
    if (cls === "id") cls = KW[lang].has(tok) ? "k" : TY[lang].has(tok) ? "t" : text[re.lastIndex] === "(" ? "f" : null;
    if (cls) { const s = document.createElement("span"); s.className = "tk-" + cls; s.textContent = tok; frag.append(s); }
    else frag.append(tok);
    last = re.lastIndex;
  }
  frag.append(text.slice(last) + "\n");
  hlc.replaceChildren(frag);
}
let lines = 0;
function gutter() {
  const n = ed.value.split("\n").length;
  if (n !== lines) { lines = n; gut.textContent = Array.from({ length: n }, (_, i) => i + 1).join("\n"); }
}
function status() {
  const p = ed.selectionStart, before = ed.value.slice(0, p);
  $("pos").textContent = "Qator " + before.split("\n").length + ", Ustun " + (p - before.lastIndexOf("\n")) + " · " + ed.value.length + " belgi";
}
let raf = 0;
function refresh() {
  highlight(); gutter(); status();
  hl.scrollTop = ed.scrollTop; hl.scrollLeft = ed.scrollLeft; gut.scrollTop = ed.scrollTop;
}
function onEdit() {
  S.codes[S.lang] = ed.value; save();
  cancelAnimationFrame(raf); raf = requestAnimationFrame(refresh);
}
ed.addEventListener("input", onEdit);
ed.addEventListener("scroll", () => { hl.scrollTop = ed.scrollTop; hl.scrollLeft = ed.scrollLeft; gut.scrollTop = ed.scrollTop; });
["keyup", "click", "select"].forEach((ev) => ed.addEventListener(ev, status));

/* ---------- Tahrirlash qulayliklari ---------- */
function insert(t) {
  ed.focus();
  if (!document.execCommand("insertText", false, t)) {       // undo saqlanadi
    ed.setRangeText(t, ed.selectionStart, ed.selectionEnd, "end");
    ed.dispatchEvent(new Event("input"));
  }
}
function autoIndent() {
  const s = ed.selectionStart, v = ed.value;
  const ls = s ? v.lastIndexOf("\n", s - 1) + 1 : 0;
  const ind = v.slice(ls, s).match(/^[ \t]*/)[0];
  const prev = v[s - 1], next = v[ed.selectionEnd];
  const extra = prev === "{" || (S.lang === "python" && prev === ":") ? "    " : "";
  const pair = prev === "{" && next === "}";
  insert("\n" + ind + extra + (pair ? "\n" + ind : ""));
  if (pair) { const p = ed.selectionStart - (1 + ind.length); ed.selectionStart = ed.selectionEnd = p; }
}
function toggleComment() {
  const p = S.lang === "python" ? "# " : "// ", pt = p.trim();
  const a = ed.selectionStart, b = ed.selectionEnd, v = ed.value;
  const ls = a ? v.lastIndexOf("\n", a - 1) + 1 : 0;
  let le = v.indexOf("\n", b); if (le < 0) le = v.length;
  const arr = v.slice(ls, le).split("\n");
  const all = arr.every((l) => l.trim() === "" || l.trimStart().startsWith(pt));
  const res = arr.map((l) => {
    if (l.trim() === "") return l;
    const i = l.length - l.trimStart().length, rest = l.slice(i);
    return all ? l.slice(0, i) + (rest.startsWith(p) ? rest.slice(p.length) : rest.slice(pt.length)) : l.slice(0, i) + p + rest;
  });
  ed.setRangeText(res.join("\n"), ls, le, "select");
  ed.dispatchEvent(new Event("input"));
}
ed.addEventListener("keydown", (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (e.key === "Tab" && !e.shiftKey) { e.preventDefault(); insert("    "); }
  else if (e.key === "Enter" && !mod) { e.preventDefault(); autoIndent(); }
  else if (e.key === "/" && mod) { e.preventDefault(); toggleComment(); }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(); }
});

/* ---------- Til, sozlamalar, namunalar ---------- */
function fillOptions() {
  const c = S.lang !== "python";
  $("copts").hidden = !c;
  $("asm").hidden = !c;
  if (c) {
    $("std").replaceChildren(...STD[S.lang].map((s) => new Option(s, s)));
    $("std").value = S.std[S.lang];
  }
  $("opt").value = S.opt; $("san").checked = S.san; $("time").value = String(S.time);
  const ex = $("ex");
  ex.replaceChildren(new Option("Namunalar…", ""), ...EX[S.lang].map(([n], i) => new Option(n, String(i))));
}
function setLang(l) {
  S.codes[S.lang] = ed.value; S.lang = l; ed.value = S.codes[l];
  fillOptions(); refresh(); save();
}
$("lang").addEventListener("change", () => setLang($("lang").value));
$("std").addEventListener("change", () => { S.std[S.lang] = $("std").value; save(); });
$("opt").addEventListener("change", () => { S.opt = $("opt").value; save(); });
$("san").addEventListener("change", () => { S.san = $("san").checked; save(); });
$("time").addEventListener("change", () => { S.time = Number($("time").value); save(); });
$("ex").addEventListener("change", () => {
  const i = $("ex").value;
  if (i !== "" && confirm("Joriy kod namuna bilan almashtirilsinmi?")) {
    ed.value = EX[S.lang][Number(i)][1]; onEdit();
  }
  $("ex").value = "";
});
$("stdin").addEventListener("input", () => { S.stdin = $("stdin").value; $("stdin").classList.remove("need"); save(); });

/* ---------- Fayl: saqlash va ochish ---------- */
const EXT = { c: "c", cpp: "cpp", python: "py" };
$("save").addEventListener("click", () => {
  const url = URL.createObjectURL(new Blob([ed.value], { type: "text/plain" }));
  const a = document.createElement("a");
  a.href = url; a.download = "main." + EXT[S.lang];
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$("file").addEventListener("change", () => {
  const f = $("file").files[0];
  $("file").value = "";
  if (!f) return;
  if (f.size > MAXC) { $("meta").textContent = "Fayl juda katta (maks. 100 KB)."; return; }
  const r = new FileReader();
  r.onload = () => {
    if (typeof r.result !== "string") return;
    const n = f.name.toLowerCase();
    const l = n.endsWith(".py") ? "python" : /\.(cpp|cc|hpp)$/.test(n) ? "cpp" : n.endsWith(".c") || n.endsWith(".h") ? "c" : S.lang;
    $("lang").value = l; setLang(l);
    ed.value = r.result; onEdit();
  };
  r.readAsText(f);
});

/* ---------- Ko'rinish: shrift va mavzu ---------- */
function applyView() {
  document.documentElement.style.setProperty("--fs", S.fs + "px");
  if (S.theme === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.dataset.theme = S.theme;
}
$("fm").addEventListener("click", () => { S.fs = Math.max(11, S.fs - 1); applyView(); save(); });
$("fp").addEventListener("click", () => { S.fs = Math.min(24, S.fs + 1); applyView(); save(); });
$("theme").addEventListener("click", () => {
  const dark = S.theme === "dark" || (S.theme === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  S.theme = dark ? "light" : "dark"; applyView(); save();
});

/* ---------- Ishga tushirish va natija ---------- */
function section(label) {
  const d = document.createElement("div"); d.className = "sec"; d.textContent = label; out.append(d);
}
function block(cls, text) {
  const p = document.createElement("pre"); p.className = cls; p.textContent = text; out.append(p);
}
function render(d) {
  out.replaceChildren();
  if (d.compile) { section("KOMPILYATOR"); block(d.compile_failed ? "bad" : "warn", d.compile); }
  if (!d.compile_failed) {
    section("NATIJA");
    block(d.code === 0 && !d.timeout ? "" : "bad", d.output || "(chiqish yo'q)");
    if (d.truncated) block("warn", "[natija qisqartirildi]");
    if (d.timeout) block("bad", "[vaqt tugadi]");
  }
  const ok = d.code === 0 && !d.timeout;
  let m = (ok ? "✔" : "✖") + " chiqish kodi: " + d.code;
  if (d.ms_compile) m += " · kompilyatsiya " + d.ms_compile + " ms";
  if (!d.compile_failed) m += " · ishlash " + d.ms_run + " ms";
  $("meta").textContent = m;
}
let running = false;
async function run() {
  if (running) return;
  running = true; $("run").disabled = true; $("meta").textContent = "Ishlayapti…";
  const stdin = $("stdin").value;
  const needs = /\b(scanf|fgets|getline|gets|getchar|cin|input)\b/.test(ed.value) && stdin.trim() === "";
  $("stdin").classList.toggle("need", needs);
  try {
    const r = await fetch("/run", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Token": T },
      body: JSON.stringify({ lang: S.lang, code: ed.value, stdin,
        opts: { std: S.std[S.lang], opt: S.opt, san: S.san, time: S.time, args: $("args").value } })
    });
    const d = await r.json();
    if (!r.ok || d.error) {
      out.replaceChildren(); block("bad", d.error || "So'rov rad etildi."); $("meta").textContent = "Xato.";
    } else {
      render(d);
      if (needs) { section("ESLATMA"); block("warn", "Dasturingiz kiritish o'qiydi, lekin KIRISH (stdin) maydoni bo'sh. Ma'lumotni o'ng yuqoridagi maydonga yozing."); }
    }
  } catch (e) {
    out.replaceChildren(); block("bad", "Serverga ulanib bo'lmadi."); $("meta").textContent = "Xato.";
  } finally { running = false; $("run").disabled = false; }
}
$("run").addEventListener("click", run);
$("clear").addEventListener("click", () => { out.replaceChildren(); $("meta").textContent = "Tayyor."; });
$("copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(out.innerText); $("meta").textContent = "Nusxa olindi."; }
  catch (e) { $("meta").textContent = "Nusxa olib bo'lmadi."; }
});

/* ---------- So'rov yordamchisi, Assembly, Topshiriqlar ---------- */
async function post(stdin, extra) {
  const r = await fetch("/run", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Token": T },
    body: JSON.stringify({ lang: S.lang, code: ed.value, stdin,
      opts: { std: S.std[S.lang], opt: S.opt, san: S.san, time: S.time, args: $("args").value, ...extra } })
  });
  try { const d = await r.json(); return r.ok ? d : { error: d.error || "So'rov rad etildi." }; }
  catch (e) { return { error: "Javob o'qilmadi." }; }
}
$("asm").addEventListener("click", async () => {
  if (running) return;
  running = true; $("meta").textContent = "Assembly tayyorlanmoqda…";
  try {
    const d = await post("", { asm: true });
    out.replaceChildren();
    if (d.error) block("bad", d.error);
    else if (d.compile_failed) { section("KOMPILYATOR"); block("bad", d.compile); }
    else {
      if (d.compile) { section("KOMPILYATOR"); block("warn", d.compile); }
      section("ASSEMBLY (" + S.opt + ")"); block("", d.asm || "(bo'sh)");
    }
    $("meta").textContent = "Assembly tayyor. Optimizatsiyani o'zgartirib solishtiring.";
  } catch (e) { out.replaceChildren(); block("bad", "Serverga ulanib bo'lmadi."); }
  finally { running = false; }
});

const TASKS = [
  { title: "Ikki son yig'indisi", desc: "Bo'sh joy bilan ajratilgan ikkita butun son kiritiladi. Yig'indisini chiqaring.",
    tests: [["2 3", "5"], ["-4 10", "6"], ["100 200", "300"]] },
  { title: "Juft yoki toq", desc: "Bitta butun son kiritiladi. Juft bo'lsa juft, toq bo'lsa toq so'zini chiqaring.",
    tests: [["4", "juft"], ["7", "toq"], ["0", "juft"]] },
  { title: "Faktorial", desc: "n (0 dan 15 gacha) kiritiladi. n! ni chiqaring.",
    tests: [["0", "1"], ["5", "120"], ["10", "3628800"]] },
  { title: "Tub son", desc: "n (2 dan katta yoki teng) kiritiladi. Tub bo'lsa ha, aks holda yoq chiqaring.",
    tests: [["2", "ha"], ["15", "yoq"], ["97", "ha"]] },
  { title: "Satrni teskari yozish", desc: "Bo'sh joysiz bitta so'z kiritiladi (lotin harflari). Uni teskari tartibda chiqaring.",
    tests: [["salom", "molas"], ["abc", "cba"], ["a", "a"]] }
];
const SKEL = {
  c: "#include <stdio.h>\n\nint main(void) {\n    // Yechimingiz shu yerda (so'rov matnisiz, faqat natijani chiqaring)\n    return 0;\n}\n",
  cpp: "#include <iostream>\n\nint main() {\n    // Yechimingiz shu yerda (so'rov matnisiz, faqat natijani chiqaring)\n}\n",
  python: "# Yechimingiz shu yerda (so'rov matnisiz, faqat natijani chiqaring)\n"
};
let curTask = -1;
$("task").replaceChildren(new Option("Topshiriqlar…", ""), ...TASKS.map((t, i) => new Option(t.title, String(i))));
$("task").addEventListener("change", () => {
  const v = $("task").value;
  if (v === "") { curTask = -1; $("test").hidden = true; return; }
  if (!confirm("Joriy kod topshiriq shabloni bilan almashtirilsinmi?")) { $("task").value = curTask < 0 ? "" : String(curTask); return; }
  curTask = Number(v); ed.value = SKEL[S.lang]; onEdit(); $("test").hidden = false;
  const t = TASKS[curTask];
  out.replaceChildren(); section("TOPSHIRIQ: " + t.title); block("", t.desc + "\nTestlar soni: " + t.tests.length);
  $("meta").textContent = "Yechib, «Tekshirish» tugmasini bosing.";
});
$("test").addEventListener("click", async () => {
  if (running || curTask < 0) return;
  running = true; $("test").disabled = true; $("meta").textContent = "Tekshirilmoqda…";
  const t = TASKS[curTask];
  out.replaceChildren(); section("TOPSHIRIQ: " + t.title);
  let pass = 0;
  try {
    for (let i = 0; i < t.tests.length; i++) {
      const [inp, exp] = t.tests[i];
      const d = await post(inp, {});
      if (d.error) { block("bad", d.error); break; }
      if (d.compile_failed) { section("KOMPILYATOR"); block("bad", d.compile); break; }
      const got = (d.output || "").trim(), ok = got === exp && d.code === 0 && !d.timeout;
      if (ok) pass++;
      block(ok ? "ok" : "bad", (ok ? "✔" : "✖") + " Test " + (i + 1) + ": kirish «" + inp + "»" +
        (ok ? "" : "\n   kutilgan: " + exp + "\n   chiqdi:    " + (d.timeout ? "[vaqt tugadi]" : got.slice(0, 200))));
    }
    $("meta").textContent = pass + "/" + t.tests.length + " test o'tdi" + (pass === t.tests.length ? " 🎉" : "");
  } catch (e) { block("bad", "Serverga ulanib bo'lmadi."); }
  finally { running = false; $("test").disabled = false; }
});

/* ---------- Boshlash ---------- */
$("lang").value = S.lang; $("stdin").value = S.stdin; ed.value = S.codes[S.lang];
applyView(); fillOptions(); refresh();
</script></body></html>"""

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "LiveCode"
    def log_message(self, *a): pass         # logga kodni yozmaymiz

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(data)

    def _deny(self, code=403):
        self._send(code, json.dumps({"error": "Ruxsat yo'q."}))

    def _host_ok(self):                     # DNS rebinding hujumidan himoya
        return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def do_GET(self):
        u = urlparse(self.path)
        tok = parse_qs(u.query).get("t", [""])[0]
        if not self._host_ok() or u.path != "/" or not secrets.compare_digest(tok, TOKEN):
            return self._deny()
        self._send(200, PAGE.replace("__TOKEN__", TOKEN), "text/html; charset=utf-8")

    def do_POST(self):
        if not self._host_ok() or self.path != "/run":
            return self._deny()
        if not secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN):
            return self._deny()
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self._deny(415)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 < n <= 300_000:
                return self._deny(413)
            d = json.loads(self.rfile.read(n))
            lang, code, stdin = d.get("lang"), d.get("code"), d.get("stdin", "")
            opts = d.get("opts", {})
            if (lang not in FILES or not isinstance(code, str) or not isinstance(stdin, str)
                    or not isinstance(opts, dict) or len(code) > MAX_CODE or len(stdin) > MAX_STDIN):
                return self._send(400, json.dumps({"error": "Noto'g'ri kirish ma'lumoti."}))
            self._send(200, json.dumps(execute(lang, code, stdin, opts)))
        except (ValueError, json.JSONDecodeError):
            self._send(400, json.dumps({"error": "Noto'g'ri so'rov."}))
        except Exception:                   # ichki xatolarni ko'rsatmaymiz
            self._send(500, json.dumps({"error": "Ichki xato."}))

if __name__ == "__main__":
    url = f"http://127.0.0.1:{PORT}/?t={TOKEN}"
    srv = http.server.HTTPServer((HOST, PORT), Handler)
    print("LiveCode ishga tushdi:", url, "\nTo'xtatish: Ctrl+C")
    try:
        webbrowser.open(url)
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nTo'xtatildi.")
    finally:
        srv.server_close()
