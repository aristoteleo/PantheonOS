const style=document.createElement('style');style.textContent=".shade[data-v-a1a4179b]{position:absolute;inset:0;z-index:5;background:#0005;display:grid;place-items:center;padding:20px}section[data-v-a1a4179b]{background:var(--surface,#fff);color:var(--text,#222);border:1px solid var(--border,#8885);border-radius:10px;width:min(520px,100%);max-height:90%;display:flex;flex-direction:column;padding:12px;gap:10px;box-sizing:border-box}header[data-v-a1a4179b],form[data-v-a1a4179b]{display:flex;align-items:center;gap:8px}header strong[data-v-a1a4179b]{flex:1}input[data-v-a1a4179b]{min-width:0;flex:1}button[data-v-a1a4179b],input[data-v-a1a4179b]{font:inherit;color:inherit;border:1px solid var(--border,#8885);border-radius:5px;padding:6px 8px;background:transparent}button[data-v-a1a4179b]{cursor:pointer}.files[data-v-a1a4179b]{overflow:auto;min-height:100px}.files button[data-v-a1a4179b]{display:block;text-align:left;width:100%;border:0}.files button[data-v-a1a4179b]:hover{background:#8882}p[data-v-a1a4179b]{font-size:12px}[role=alert][data-v-a1a4179b]{color:#b54e30}.imagej[data-v-b2d7c380]{position:relative;display:flex;flex-direction:column;width:100%;height:100%;background:var(--raised)}.bar[data-v-b2d7c380]{display:flex;align-items:center;gap:10px;padding:6px 9px;border-bottom:1px solid var(--border);background:var(--surface);flex:none;font-size:11.5px}.open[data-v-b2d7c380]{display:flex;align-items:center;gap:6px;height:24px;padding:0 10px;border:1px solid var(--border);border-radius:6px;background:var(--hover);color:var(--text);font-size:11.5px;cursor:pointer;flex:none}.open[data-v-b2d7c380]:hover:not(:disabled){background:var(--hover)}.open[data-v-b2d7c380]:disabled{opacity:.45;cursor:default}.current[data-v-b2d7c380]{color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.dims[data-v-b2d7c380]{color:var(--text-faint);font-variant-numeric:tabular-nums;flex:none}.busy[data-v-b2d7c380]{color:#d0a029;flex:none;font-variant-numeric:tabular-nums}.hint[data-v-b2d7c380]{color:var(--text-faint);flex:none}.degraded[data-v-b2d7c380]{flex:none;padding:1px 7px;border-radius:9px;background:#d0a02926;color:#d0a029;cursor:help}.failed[data-v-b2d7c380]{margin-left:auto;color:#f0a3a2;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:45%}.stage[data-v-b2d7c380]{position:relative;flex:1;min-height:0;background:#f2f2f2}.stage[data-v-b2d7c380]>div,.stage[data-v-b2d7c380] iframe{width:100%;height:100%;border:0}.overlay[data-v-b2d7c380]{position:absolute;inset:32px 0 0;z-index:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;padding:24px;background:var(--raised);color:var(--text-faint);font-size:12.5px;text-align:center}.failed-overlay[data-v-b2d7c380]{color:#f0a3a2}.overlay p[data-v-b2d7c380]{margin:0;max-width:420px}.note[data-v-b2d7c380]{font-size:11px;opacity:.75}\n";document.head.appendChild(style);
// @__NO_SIDE_EFFECTS__
function Xn(e) {
  const t = /* @__PURE__ */ Object.create(null);
  for (const n of e.split(",")) t[n] = 1;
  return (n) => n in t;
}
const k = {}, bt = [], Ke = () => {
}, Zs = () => !1, mn = (e) => e.charCodeAt(0) === 111 && e.charCodeAt(1) === 110 && // uppercase letter
(e.charCodeAt(2) > 122 || e.charCodeAt(2) < 97), _n = (e) => e.startsWith("onUpdate:"), oe = Object.assign, Zn = (e, t) => {
  const n = e.indexOf(t);
  n > -1 && e.splice(n, 1);
}, ai = Object.prototype.hasOwnProperty, K = (e, t) => ai.call(e, t), $ = Array.isArray, it = (e) => Bt(e) === "[object Map]", on = (e) => Bt(e) === "[object Set]", ws = (e) => Bt(e) === "[object Date]", F = (e) => typeof e == "function", Y = (e) => typeof e == "string", Ve = (e) => typeof e == "symbol", W = (e) => e !== null && typeof e == "object", Qs = (e) => (W(e) || F(e)) && F(e.then) && F(e.catch), er = Object.prototype.toString, Bt = (e) => er.call(e), ui = (e) => Bt(e).slice(8, -1), tr = (e) => Bt(e) === "[object Object]", Qn = (e) => Y(e) && e !== "NaN" && e[0] !== "-" && "" + parseInt(e, 10) === e, Rt = /* @__PURE__ */ Xn(
  // the leading comma is intentional so empty string "" is also included
  ",key,ref,ref_for,ref_key,onVnodeBeforeMount,onVnodeMounted,onVnodeBeforeUpdate,onVnodeUpdated,onVnodeBeforeUnmount,onVnodeUnmounted"
), yn = (e) => {
  const t = /* @__PURE__ */ Object.create(null);
  return ((n) => t[n] || (t[n] = e(n)));
}, di = /-\w/g, Te = yn(
  (e) => e.replace(di, (t) => t.slice(1).toUpperCase())
), hi = /\B([A-Z])/g, ot = yn(
  (e) => e.replace(hi, "-$1").toLowerCase()
), nr = yn((e) => e.charAt(0).toUpperCase() + e.slice(1)), An = yn(
  (e) => e ? `on${nr(e)}` : ""
), Ue = (e, t) => !Object.is(e, t), tn = (e, ...t) => {
  for (let n = 0; n < e.length; n++)
    e[n](...t);
}, sr = (e, t, n, s = !1) => {
  Object.defineProperty(e, t, {
    configurable: !0,
    enumerable: !1,
    writable: s,
    value: n
  });
}, es = (e) => {
  const t = parseFloat(e);
  return isNaN(t) ? e : t;
};
let xs;
const vn = () => xs || (xs = typeof globalThis < "u" ? globalThis : typeof self < "u" ? self : typeof window < "u" ? window : typeof global < "u" ? global : {});
function ts(e) {
  if ($(e)) {
    const t = {};
    for (let n = 0; n < e.length; n++) {
      const s = e[n], r = Y(s) ? _i(s) : ts(s);
      if (r)
        for (const i in r)
          t[i] = r[i];
    }
    return t;
  } else if (Y(e) || W(e))
    return e;
}
const pi = /;(?![^(]*\))/g, gi = /:([^]+)/, mi = /\/\*[^]*?\*\//g;
function _i(e) {
  const t = {};
  return e.replace(mi, "").split(pi).forEach((n) => {
    if (n) {
      const s = n.split(gi);
      s.length > 1 && (t[s[0].trim()] = s[1].trim());
    }
  }), t;
}
function ns(e) {
  let t = "";
  if (Y(e))
    t = e;
  else if ($(e))
    for (let n = 0; n < e.length; n++) {
      const s = ns(e[n]);
      s && (t += s + " ");
    }
  else if (W(e))
    for (const n in e)
      e[n] && (t += n + " ");
  return t.trim();
}
const yi = "itemscope,allowfullscreen,formnovalidate,ismap,nomodule,novalidate,readonly", vi = /* @__PURE__ */ Xn(yi);
function rr(e) {
  return !!e || e === "";
}
function bi(e, t) {
  if (e.length !== t.length) return !1;
  let n = !0;
  for (let s = 0; n && s < e.length; s++)
    n = bn(e[s], t[s]);
  return n;
}
function Ss(e, t) {
  if (e.size !== t.size) return !1;
  const n = Array.from(t), s = new Uint8Array(n.length);
  for (const r of e) {
    let i = -1;
    for (let o = 0; o < n.length; o++)
      if (!s[o] && bn(r, n[o])) {
        i = o;
        break;
      }
    if (i < 0) return !1;
    s[i] = 1;
  }
  return !0;
}
function bn(e, t) {
  if (e === t) return !0;
  let n = ws(e), s = ws(t);
  if (n || s)
    return n && s ? e.getTime() === t.getTime() : !1;
  if (n = Ve(e), s = Ve(t), n || s)
    return e === t;
  if (n = $(e), s = $(t), n || s)
    return n && s ? bi(e, t) : !1;
  if (n = W(e), s = W(t), n || s) {
    if (!n || !s)
      return !1;
    if (n = it(e), s = it(t), n || s || (n = on(e), s = on(t), n || s))
      return n && s ? Ss(e, t) : !1;
    const r = Object.keys(e).length, i = Object.keys(t).length;
    if (r !== i)
      return !1;
    for (const o in e) {
      const l = e.hasOwnProperty(o), c = t.hasOwnProperty(o);
      if (l && !c || !l && c || !bn(e[o], t[o]))
        return !1;
    }
  }
  return String(e) === String(t);
}
const ir = (e) => !!(e && e.__v_isRef === !0), Ie = (e) => Y(e) ? e : e == null ? "" : $(e) || W(e) && (e.toString === er || !F(e.toString)) ? ir(e) ? Ie(e.value) : JSON.stringify(e, or, 2) : String(e), or = (e, t) => ir(t) ? or(e, t.value) : it(t) ? {
  [`Map(${t.size})`]: [...t.entries()].reduce(
    (n, [s, r], i) => (n[On(s, i) + " =>"] = r, n),
    {}
  )
} : on(t) ? {
  [`Set(${t.size})`]: [...t.values()].map((n) => On(n))
} : Ve(t) ? On(t) : W(t) && !$(t) && !tr(t) ? String(t) : t, On = (e, t = "") => {
  var n;
  return (
    // Symbol.description in es2019+ so we need to cast here to pass
    // the lib: es2016 check
    Ve(e) ? `Symbol(${(n = e.description) != null ? n : t})` : e
  );
};
let re;
class wi {
  // TODO isolatedDeclarations "__v_skip"
  constructor(t = !1) {
    this.detached = t, this._active = !0, this._on = 0, this.effects = [], this.cleanups = [], this._isPaused = !1, this._warnOnRun = !0, this.__v_skip = !0, !t && re && (re.active ? (this.parent = re, this.index = (re.scopes || (re.scopes = [])).push(
      this
    ) - 1) : (this._active = !1, this._warnOnRun = !1));
  }
  get active() {
    return this._active;
  }
  pause() {
    if (this._active) {
      this._isPaused = !0;
      let t, n;
      if (this.scopes) {
        const s = this.scopes.slice();
        for (t = 0, n = s.length; t < n; t++)
          s[t].pause();
      }
      for (t = 0, n = this.effects.length; t < n; t++)
        this.effects[t].pause();
    }
  }
  /**
   * Resumes the effect scope, including all child scopes and effects.
   */
  resume() {
    if (this._active && this._isPaused) {
      this._isPaused = !1;
      let t, n;
      if (this.scopes) {
        const r = this.scopes.slice();
        for (t = 0, n = r.length; t < n; t++)
          r[t].resume();
      }
      const s = this.effects.slice();
      for (t = 0, n = s.length; t < n; t++)
        s[t].resume();
    }
  }
  run(t) {
    if (this._active) {
      const n = re;
      try {
        return re = this, t();
      } finally {
        re = n;
      }
    }
  }
  /**
   * This should only be called on non-detached scopes
   * @internal
   */
  on() {
    ++this._on === 1 && (this.prevScope = re, re = this);
  }
  /**
   * This should only be called on non-detached scopes
   * @internal
   */
  off() {
    if (this._on > 0 && --this._on === 0) {
      if (re === this)
        re = this.prevScope;
      else {
        let t = re;
        for (; t; ) {
          if (t.prevScope === this) {
            t.prevScope = this.prevScope;
            break;
          }
          t = t.prevScope;
        }
      }
      this.prevScope = void 0;
    }
  }
  stop(t) {
    if (this._active) {
      this._active = !1;
      let n, s;
      for (n = 0, s = this.effects.length; n < s; n++)
        this.effects[n].stop();
      for (this.effects.length = 0, n = 0, s = this.cleanups.length; n < s; n++)
        this.cleanups[n]();
      if (this.cleanups.length = 0, this.scopes) {
        const r = this.scopes.slice();
        for (n = 0, s = r.length; n < s; n++)
          r[n].stop(!0);
        this.scopes.length = 0;
      }
      if (!this.detached && this.parent && !t) {
        const r = this.parent.scopes.pop();
        r && r !== this && (this.parent.scopes[this.index] = r, r.index = this.index);
      }
      this.parent = void 0;
    }
  }
}
function xi() {
  return re;
}
let q;
const Pn = /* @__PURE__ */ new WeakSet();
class lr {
  constructor(t) {
    this.fn = t, this.deps = void 0, this.depsTail = void 0, this.flags = 5, this.next = void 0, this.cleanup = void 0, this.scheduler = void 0, re && (re.active ? re.effects.push(this) : this.flags &= -2);
  }
  pause() {
    this.flags |= 64;
  }
  resume() {
    this.flags & 64 && (this.flags &= -65, Pn.has(this) && (Pn.delete(this), this.trigger()));
  }
  /**
   * @internal
   */
  notify() {
    this.flags & 2 && !(this.flags & 32) || this.flags & 8 || fr(this);
  }
  run() {
    if (!(this.flags & 1))
      return this.fn();
    this.flags |= 2, Es(this), ar(this);
    const t = q, n = Ae;
    q = this, Ae = !0;
    try {
      return this.fn();
    } finally {
      ur(this), q = t, Ae = n, this.flags &= -3;
    }
  }
  stop() {
    if (this.flags & 1) {
      for (let t = this.deps; t; t = t.nextDep)
        is(t);
      this.deps = this.depsTail = void 0, Es(this), this.onStop && this.onStop(), this.flags &= -2;
    }
  }
  trigger() {
    this.flags & 64 ? Pn.add(this) : this.scheduler ? this.scheduler() : this.runIfDirty();
  }
  /**
   * @internal
   */
  runIfDirty() {
    Kn(this) && this.run();
  }
  get dirty() {
    return Kn(this);
  }
}
let cr = 0, Ft, jt;
function fr(e, t = !1) {
  if (e.flags |= 8, t) {
    e.next = jt, jt = e;
    return;
  }
  e.next = Ft, Ft = e;
}
function ss() {
  cr++;
}
function rs() {
  if (--cr > 0)
    return;
  if (jt) {
    let t = jt;
    for (jt = void 0; t; ) {
      const n = t.next;
      t.next = void 0, t.flags &= -9, t = n;
    }
  }
  let e;
  for (; Ft; ) {
    let t = Ft;
    for (Ft = void 0; t; ) {
      const n = t.next;
      if (t.next = void 0, t.flags &= -9, t.flags & 1)
        try {
          t.trigger();
        } catch (s) {
          e || (e = s);
        }
      t = n;
    }
  }
  if (e) throw e;
}
function ar(e) {
  for (let t = e.deps; t; t = t.nextDep)
    t.version = -1, t.prevActiveLink = t.dep.activeLink, t.dep.activeLink = t;
}
function ur(e) {
  let t, n = e.depsTail, s = n;
  for (; s; ) {
    const r = s.prevDep;
    s.version === -1 ? (s === n && (n = r), is(s), Si(s)) : t = s, s.dep.activeLink = s.prevActiveLink, s.prevActiveLink = void 0, s = r;
  }
  e.deps = t, e.depsTail = n;
}
function Kn(e) {
  for (let t = e.deps; t; t = t.nextDep)
    if (t.dep.version !== t.version || t.dep.computed && (dr(t.dep.computed) || t.dep.version !== t.version))
      return !0;
  return !!e._dirty;
}
function dr(e) {
  if (e.flags & 4 && !(e.flags & 16) || (e.flags &= -17, e.globalVersion === Ht) || (e.globalVersion = Ht, !e.isSSR && e.flags & 128 && (!e.deps && !e._dirty || !Kn(e))))
    return;
  e.flags |= 2;
  const t = e.dep, n = q, s = Ae;
  q = e, Ae = !0;
  try {
    ar(e);
    const r = e.fn(e._value);
    (t.version === 0 || Ue(r, e._value)) && (e.flags |= 128, e._value = r, t.version++);
  } catch (r) {
    throw t.version++, r;
  } finally {
    q = n, Ae = s, ur(e), e.flags &= -3;
  }
}
function is(e, t = !1) {
  const { dep: n, prevSub: s, nextSub: r } = e;
  if (s && (s.nextSub = r, e.prevSub = void 0), r && (r.prevSub = s, e.nextSub = void 0), n.subs === e && (n.subs = s, !s && n.computed)) {
    n.computed.flags &= -5;
    for (let i = n.computed.deps; i; i = i.nextDep)
      is(i, !0);
  }
  !t && !--n.sc && n.map && n.map.delete(n.key);
}
function Si(e) {
  const { prevDep: t, nextDep: n } = e;
  t && (t.nextDep = n, e.prevDep = void 0), n && (n.prevDep = t, e.nextDep = void 0);
}
let Ae = !0;
const hr = [];
function Ze() {
  hr.push(Ae), Ae = !1;
}
function Qe() {
  const e = hr.pop();
  Ae = e === void 0 ? !0 : e;
}
function Es(e) {
  const { cleanup: t } = e;
  if (e.cleanup = void 0, t) {
    const n = q;
    q = void 0;
    try {
      t();
    } finally {
      q = n;
    }
  }
}
let Ht = 0;
class Ei {
  constructor(t, n) {
    this.sub = t, this.dep = n, this.version = n.version, this.nextDep = this.prevDep = this.nextSub = this.prevSub = this.prevActiveLink = void 0;
  }
}
class os {
  // TODO isolatedDeclarations "__v_skip"
  constructor(t) {
    this.computed = t, this.version = 0, this.activeLink = void 0, this.subs = void 0, this.map = void 0, this.key = void 0, this.sc = 0, this.__v_skip = !0;
  }
  track(t) {
    if (!q || !Ae || q === this.computed)
      return;
    let n = this.activeLink;
    if (n === void 0 || n.sub !== q)
      n = this.activeLink = new Ei(q, this), q.deps ? (n.prevDep = q.depsTail, q.depsTail.nextDep = n, q.depsTail = n) : q.deps = q.depsTail = n, pr(n);
    else if (n.version === -1 && (n.version = this.version, n.nextDep)) {
      const s = n.nextDep;
      s.prevDep = n.prevDep, n.prevDep && (n.prevDep.nextDep = s), n.prevDep = q.depsTail, n.nextDep = void 0, q.depsTail.nextDep = n, q.depsTail = n, q.deps === n && (q.deps = s);
    }
    return n;
  }
  trigger(t) {
    this.version++, Ht++, this.notify(t);
  }
  notify(t) {
    ss();
    try {
      for (let n = this.subs; n; n = n.prevSub)
        n.sub.notify() && n.sub.dep.notify();
    } finally {
      rs();
    }
  }
}
function pr(e) {
  if (e.dep.sc++, e.sub.flags & 4) {
    const t = e.dep.computed;
    if (t && !e.dep.subs) {
      t.flags |= 20;
      for (let s = t.deps; s; s = s.nextDep)
        pr(s);
    }
    const n = e.dep.subs;
    n !== e && (e.prevSub = n, n && (n.nextSub = e)), e.dep.subs = e;
  }
}
const Vn = /* @__PURE__ */ new WeakMap(), dt = /* @__PURE__ */ Symbol(
  ""
), Jn = /* @__PURE__ */ Symbol(
  ""
), Ut = /* @__PURE__ */ Symbol(
  ""
);
function ce(e, t, n) {
  if (Ae && q) {
    let s = Vn.get(e);
    s || Vn.set(e, s = /* @__PURE__ */ new Map());
    let r = s.get(n);
    r || (s.set(n, r = new os()), r.map = s, r.key = n), r.track();
  }
}
function ze(e, t, n, s, r, i) {
  const o = Vn.get(e);
  if (!o) {
    Ht++;
    return;
  }
  const l = (c) => {
    c && c.trigger();
  };
  if (ss(), t === "clear")
    o.forEach(l);
  else {
    const c = $(e), d = c && Qn(n);
    if (c && n === "length") {
      const u = Number(s);
      o.forEach((p, w) => {
        (w === "length" || w === Ut || !Ve(w) && w >= u) && l(p);
      });
    } else
      switch ((n !== void 0 || o.has(void 0)) && l(o.get(n)), d && l(o.get(Ut)), t) {
        case "add":
          c ? d && l(o.get("length")) : (l(o.get(dt)), it(e) && l(o.get(Jn)));
          break;
        case "delete":
          c || (l(o.get(dt)), it(e) && l(o.get(Jn)));
          break;
        case "set":
          it(e) && l(o.get(dt));
          break;
      }
  }
  rs();
}
function _t(e) {
  const t = /* @__PURE__ */ U(e);
  return t === e ? t : (ce(t, "iterate", Ut), /* @__PURE__ */ xe(e) ? t : t.map(Oe));
}
function wn(e) {
  return ce(e = /* @__PURE__ */ U(e), "iterate", Ut), e;
}
function De(e, t) {
  return /* @__PURE__ */ et(e) ? St(/* @__PURE__ */ ht(e) ? Oe(t) : t) : Oe(t);
}
const Ci = {
  __proto__: null,
  [Symbol.iterator]() {
    return Mn(this, Symbol.iterator, (e) => De(this, e));
  },
  concat(...e) {
    return _t(this).concat(
      ...e.map((t) => $(t) ? _t(t) : t)
    );
  },
  entries() {
    return Mn(this, "entries", (e) => (e[1] = De(this, e[1]), e));
  },
  every(e, t) {
    return ke(this, "every", e, t, void 0, arguments);
  },
  filter(e, t) {
    return ke(
      this,
      "filter",
      e,
      t,
      (n) => n.map((s) => De(this, s)),
      arguments
    );
  },
  find(e, t) {
    return ke(
      this,
      "find",
      e,
      t,
      (n) => De(this, n),
      arguments
    );
  },
  findIndex(e, t) {
    return ke(this, "findIndex", e, t, void 0, arguments);
  },
  findLast(e, t) {
    return ke(
      this,
      "findLast",
      e,
      t,
      (n) => De(this, n),
      arguments
    );
  },
  findLastIndex(e, t) {
    return ke(this, "findLastIndex", e, t, void 0, arguments);
  },
  // flat, flatMap could benefit from ARRAY_ITERATE but are not straight-forward to implement
  forEach(e, t) {
    return ke(this, "forEach", e, t, void 0, arguments);
  },
  includes(...e) {
    return $n(this, "includes", e);
  },
  indexOf(...e) {
    return $n(this, "indexOf", e);
  },
  join(e) {
    return _t(this).join(e);
  },
  // keys() iterator only reads `length`, no optimization required
  lastIndexOf(...e) {
    return $n(this, "lastIndexOf", e);
  },
  map(e, t) {
    return ke(this, "map", e, t, void 0, arguments);
  },
  pop() {
    return Tt(this, "pop");
  },
  push(...e) {
    return Tt(this, "push", e);
  },
  reduce(e, ...t) {
    return Cs(this, "reduce", e, t);
  },
  reduceRight(e, ...t) {
    return Cs(this, "reduceRight", e, t);
  },
  shift() {
    return Tt(this, "shift");
  },
  // slice could use ARRAY_ITERATE but also seems to beg for range tracking
  some(e, t) {
    return ke(this, "some", e, t, void 0, arguments);
  },
  splice(...e) {
    return Tt(this, "splice", e);
  },
  toReversed() {
    return _t(this).toReversed();
  },
  toSorted(e) {
    return _t(this).toSorted(e);
  },
  toSpliced(...e) {
    return _t(this).toSpliced(...e);
  },
  unshift(...e) {
    return Tt(this, "unshift", e);
  },
  values() {
    return Mn(this, "values", (e) => De(this, e));
  }
};
function Mn(e, t, n) {
  const s = wn(e), r = s[t]();
  return s !== e && !/* @__PURE__ */ xe(e) && (r._next = r.next, r.next = () => {
    const i = r._next();
    return i.done || (i.value = n(i.value)), i;
  }), r;
}
const Ii = Array.prototype;
function ke(e, t, n, s, r, i) {
  const o = wn(e), l = o !== e && !/* @__PURE__ */ xe(e), c = o[t];
  if (c !== Ii[t]) {
    const p = c.apply(e, i);
    return l ? Oe(p) : p;
  }
  let d = n;
  o !== e && (l ? d = function(p, w) {
    return n.call(this, De(e, p), w, e);
  } : n.length > 2 && (d = function(p, w) {
    return n.call(this, p, w, e);
  }));
  const u = c.call(o, d, s);
  return l && r ? r(u) : u;
}
function Cs(e, t, n, s) {
  const r = wn(e), i = r !== e && !/* @__PURE__ */ xe(e);
  let o = n, l = !1;
  r !== e && (i ? (l = s.length === 0, o = function(d, u, p) {
    return l && (l = !1, d = De(e, d)), n.call(this, d, De(e, u), p, e);
  }) : n.length > 3 && (o = function(d, u, p) {
    return n.call(this, d, u, p, e);
  }));
  const c = r[t](o, ...s);
  return l ? De(e, c) : c;
}
function $n(e, t, n) {
  const s = /* @__PURE__ */ U(e);
  ce(s, "iterate", Ut);
  const r = s[t](...n);
  return (r === -1 || r === !1) && /* @__PURE__ */ as(n[0]) ? (n[0] = /* @__PURE__ */ U(n[0]), s[t](...n)) : r;
}
function Tt(e, t, n = []) {
  Ze(), ss();
  const s = (/* @__PURE__ */ U(e))[t].apply(e, n);
  return rs(), Qe(), s;
}
const Ti = /* @__PURE__ */ Xn("__proto__,__v_isRef,__isVue"), gr = new Set(
  /* @__PURE__ */ Object.getOwnPropertyNames(Symbol).filter((e) => e !== "arguments" && e !== "caller").map((e) => Symbol[e]).filter(Ve)
);
function Ai(e) {
  Ve(e) || (e = String(e));
  const t = /* @__PURE__ */ U(this);
  return ce(t, "has", e), t.hasOwnProperty(e);
}
class mr {
  constructor(t = !1, n = !1) {
    this._isReadonly = t, this._isShallow = n;
  }
  get(t, n, s) {
    if (n === "__v_skip") return t.__v_skip;
    const r = this._isReadonly, i = this._isShallow;
    if (n === "__v_isReactive")
      return !r;
    if (n === "__v_isReadonly")
      return r;
    if (n === "__v_isShallow")
      return i;
    if (n === "__v_raw")
      return s === (r ? i ? Li : br : i ? vr : yr).get(t) || // receiver is not the reactive proxy, but has the same prototype
      // this means the receiver is a user proxy of the reactive proxy
      Object.getPrototypeOf(t) === Object.getPrototypeOf(s) ? t : void 0;
    const o = $(t);
    if (!r) {
      let c;
      if (o && (c = Ci[n]))
        return c;
      if (n === "hasOwnProperty")
        return Ai;
    }
    const l = Reflect.get(
      t,
      n,
      // if this is a proxy wrapping a ref, return methods using the raw ref
      // as receiver so that we don't have to call `toRaw` on the ref in all
      // its class methods
      /* @__PURE__ */ fe(t) ? t : s
    );
    if ((Ve(n) ? gr.has(n) : Ti(n)) || (r || ce(t, "get", n), i))
      return l;
    if (/* @__PURE__ */ fe(l)) {
      const c = o && Qn(n) ? l : l.value;
      return r && W(c) ? /* @__PURE__ */ Bn(c) : c;
    }
    return W(l) ? r ? /* @__PURE__ */ Bn(l) : /* @__PURE__ */ cs(l) : l;
  }
}
class _r extends mr {
  constructor(t = !1) {
    super(!1, t);
  }
  set(t, n, s, r) {
    let i = t[n];
    const o = $(t) && Qn(n);
    if (!this._isShallow) {
      const d = /* @__PURE__ */ et(i);
      if (!/* @__PURE__ */ xe(s) && !/* @__PURE__ */ et(s) && (i = /* @__PURE__ */ U(i), s = /* @__PURE__ */ U(s)), !o && /* @__PURE__ */ fe(i) && !/* @__PURE__ */ fe(s))
        return d || (i.value = s), !0;
    }
    const l = o ? Number(n) < t.length : K(t, n), c = Reflect.set(
      t,
      n,
      s,
      /* @__PURE__ */ fe(t) ? t : r
    );
    return t === /* @__PURE__ */ U(r) && c && (l ? Ue(s, i) && ze(t, "set", n, s) : ze(t, "add", n, s)), c;
  }
  deleteProperty(t, n) {
    const s = K(t, n);
    t[n];
    const r = Reflect.deleteProperty(t, n);
    return r && s && ze(t, "delete", n, void 0), r;
  }
  has(t, n) {
    const s = Reflect.has(t, n);
    return (!Ve(n) || !gr.has(n)) && ce(t, "has", n), s;
  }
  ownKeys(t) {
    return ce(
      t,
      "iterate",
      $(t) ? "length" : dt
    ), Reflect.ownKeys(t);
  }
}
class Oi extends mr {
  constructor(t = !1) {
    super(!0, t);
  }
  set(t, n) {
    return !0;
  }
  deleteProperty(t, n) {
    return !0;
  }
}
const Pi = /* @__PURE__ */ new _r(), Mi = /* @__PURE__ */ new Oi(), $i = /* @__PURE__ */ new _r(!0);
const Wn = (e) => e, zt = (e) => Reflect.getPrototypeOf(e);
function Ri(e, t, n) {
  return function(...s) {
    const r = this.__v_raw, i = /* @__PURE__ */ U(r), o = it(i), l = e === "entries" || e === Symbol.iterator && o, c = e === "keys" && o, d = r[e](...s), u = n ? Wn : t ? St : Oe;
    return !t && ce(
      i,
      "iterate",
      c ? Jn : dt
    ), oe(
      // inheriting all iterator properties
      Object.create(d),
      {
        // iterator protocol
        next() {
          const { value: p, done: w } = d.next();
          return w ? { value: p, done: w } : {
            value: l ? [u(p[0]), u(p[1])] : u(p),
            done: w
          };
        }
      }
    );
  };
}
function Yt(e) {
  return function(...t) {
    return e === "delete" ? !1 : e === "clear" ? void 0 : this;
  };
}
function Fi(e, t) {
  const n = {
    get(r) {
      const i = this.__v_raw, o = /* @__PURE__ */ U(i), l = /* @__PURE__ */ U(r);
      e || (Ue(r, l) && ce(o, "get", r), ce(o, "get", l));
      const { has: c } = zt(o), d = t ? Wn : e ? St : Oe;
      if (c.call(o, r))
        return d(i.get(r));
      if (c.call(o, l))
        return d(i.get(l));
      i !== o && i.get(r);
    },
    get size() {
      const r = this.__v_raw;
      return !e && ce(/* @__PURE__ */ U(r), "iterate", dt), r.size;
    },
    has(r) {
      const i = this.__v_raw, o = /* @__PURE__ */ U(i), l = /* @__PURE__ */ U(r);
      return e || (Ue(r, l) && ce(o, "has", r), ce(o, "has", l)), r === l ? i.has(r) : i.has(r) || i.has(l);
    },
    forEach(r, i) {
      const o = this, l = o.__v_raw, c = /* @__PURE__ */ U(l), d = t ? Wn : e ? St : Oe;
      return !e && ce(c, "iterate", dt), l.forEach((u, p) => r.call(i, d(u), d(p), o));
    }
  };
  return oe(
    n,
    e ? {
      add: Yt("add"),
      set: Yt("set"),
      delete: Yt("delete"),
      clear: Yt("clear")
    } : {
      add(r) {
        const i = /* @__PURE__ */ U(this), o = zt(i), l = /* @__PURE__ */ U(r), c = !t && !/* @__PURE__ */ xe(r) && !/* @__PURE__ */ et(r) ? l : r;
        return o.has.call(i, c) || Ue(r, c) && o.has.call(i, r) || Ue(l, c) && o.has.call(i, l) || (i.add(c), ze(i, "add", c, c)), this;
      },
      set(r, i) {
        !t && !/* @__PURE__ */ xe(i) && !/* @__PURE__ */ et(i) && (i = /* @__PURE__ */ U(i));
        const o = /* @__PURE__ */ U(this), { has: l, get: c } = zt(o);
        let d = l.call(o, r);
        d || (r = /* @__PURE__ */ U(r), d = l.call(o, r));
        const u = c.call(o, r);
        return o.set(r, i), d ? Ue(i, u) && ze(o, "set", r, i) : ze(o, "add", r, i), this;
      },
      delete(r) {
        const i = /* @__PURE__ */ U(this), { has: o, get: l } = zt(i);
        let c = o.call(i, r);
        c || (r = /* @__PURE__ */ U(r), c = o.call(i, r)), l && l.call(i, r);
        const d = i.delete(r);
        return c && ze(i, "delete", r, void 0), d;
      },
      clear() {
        const r = /* @__PURE__ */ U(this), i = r.size !== 0, o = r.clear();
        return i && ze(
          r,
          "clear",
          void 0,
          void 0
        ), o;
      }
    }
  ), [
    "keys",
    "values",
    "entries",
    Symbol.iterator
  ].forEach((r) => {
    n[r] = Ri(r, e, t);
  }), n;
}
function ls(e, t) {
  const n = Fi(e, t);
  return (s, r, i) => r === "__v_isReactive" ? !e : r === "__v_isReadonly" ? e : r === "__v_raw" ? s : Reflect.get(
    K(n, r) && r in s ? n : s,
    r,
    i
  );
}
const ji = {
  get: /* @__PURE__ */ ls(!1, !1)
}, Ni = {
  get: /* @__PURE__ */ ls(!1, !0)
}, Di = {
  get: /* @__PURE__ */ ls(!0, !1)
};
const yr = /* @__PURE__ */ new WeakMap(), vr = /* @__PURE__ */ new WeakMap(), br = /* @__PURE__ */ new WeakMap(), Li = /* @__PURE__ */ new WeakMap();
function Hi(e) {
  switch (e) {
    case "Object":
    case "Array":
      return 1;
    case "Map":
    case "Set":
    case "WeakMap":
    case "WeakSet":
      return 2;
    default:
      return 0;
  }
}
// @__NO_SIDE_EFFECTS__
function cs(e) {
  return /* @__PURE__ */ et(e) ? e : fs(
    e,
    !1,
    Pi,
    ji,
    yr
  );
}
// @__NO_SIDE_EFFECTS__
function Ui(e) {
  return fs(
    e,
    !1,
    $i,
    Ni,
    vr
  );
}
// @__NO_SIDE_EFFECTS__
function Bn(e) {
  return fs(
    e,
    !0,
    Mi,
    Di,
    br
  );
}
function fs(e, t, n, s, r) {
  if (!W(e) || e.__v_raw && !(t && e.__v_isReactive) || e.__v_skip || !Object.isExtensible(e))
    return e;
  const i = r.get(e);
  if (i)
    return i;
  const o = Hi(ui(e));
  if (o === 0)
    return e;
  const l = new Proxy(
    e,
    o === 2 ? s : n
  );
  return r.set(e, l), l;
}
// @__NO_SIDE_EFFECTS__
function ht(e) {
  return /* @__PURE__ */ et(e) ? /* @__PURE__ */ ht(e.__v_raw) : !!(e && e.__v_isReactive);
}
// @__NO_SIDE_EFFECTS__
function et(e) {
  return !!(e && e.__v_isReadonly);
}
// @__NO_SIDE_EFFECTS__
function xe(e) {
  return !!(e && e.__v_isShallow);
}
// @__NO_SIDE_EFFECTS__
function as(e) {
  return e ? !!e.__v_raw : !1;
}
// @__NO_SIDE_EFFECTS__
function U(e) {
  const t = e && e.__v_raw;
  return t ? /* @__PURE__ */ U(t) : e;
}
function Ki(e) {
  return !K(e, "__v_skip") && Object.isExtensible(e) && sr(e, "__v_skip", !0), e;
}
const Oe = (e) => W(e) ? /* @__PURE__ */ cs(e) : e, St = (e) => W(e) ? /* @__PURE__ */ Bn(e) : e;
// @__NO_SIDE_EFFECTS__
function fe(e) {
  return e ? e.__v_isRef === !0 : !1;
}
// @__NO_SIDE_EFFECTS__
function ie(e) {
  return Vi(e, !1);
}
function Vi(e, t) {
  return /* @__PURE__ */ fe(e) ? e : new Ji(e, t);
}
class Ji {
  constructor(t, n) {
    this.dep = new os(), this.__v_isRef = !0, this.__v_isShallow = !1, this._rawValue = n ? t : /* @__PURE__ */ U(t), this._value = n ? t : Oe(t), this.__v_isShallow = n;
  }
  get value() {
    return this.dep.track(), this._value;
  }
  set value(t) {
    const n = this._rawValue, s = this.__v_isShallow || /* @__PURE__ */ xe(t) || /* @__PURE__ */ et(t);
    t = s ? t : /* @__PURE__ */ U(t), Ue(t, n) && (this._rawValue = t, this._value = s ? t : Oe(t), this.dep.trigger());
  }
}
function Wi(e) {
  return /* @__PURE__ */ fe(e) ? e.value : e;
}
const Bi = {
  get: (e, t, n) => t === "__v_raw" ? e : Wi(Reflect.get(e, t, n)),
  set: (e, t, n, s) => {
    const r = e[t];
    return /* @__PURE__ */ fe(r) && !/* @__PURE__ */ fe(n) ? (r.value = n, !0) : Reflect.set(e, t, n, s);
  }
};
function wr(e) {
  return /* @__PURE__ */ ht(e) ? e : new Proxy(e, Bi);
}
class ki {
  constructor(t, n, s) {
    this.fn = t, this.setter = n, this._value = void 0, this.dep = new os(this), this.__v_isRef = !0, this.deps = void 0, this.depsTail = void 0, this.flags = 16, this.globalVersion = Ht - 1, this.next = void 0, this.effect = this, this.__v_isReadonly = !n, this.isSSR = s;
  }
  /**
   * @internal
   */
  notify() {
    if (this.flags |= 16, !(this.flags & 8) && // avoid infinite self recursion
    q !== this)
      return fr(this, !0), !0;
  }
  get value() {
    const t = this.dep.track();
    return dr(this), t && (t.version = this.dep.version), this._value;
  }
  set value(t) {
    this.setter && this.setter(t);
  }
}
// @__NO_SIDE_EFFECTS__
function Gi(e, t, n = !1) {
  let s, r;
  return F(e) ? s = e : (s = e.get, r = e.set), new ki(s, r, n);
}
const Xt = {}, ln = /* @__PURE__ */ new WeakMap();
let ut;
function qi(e, t = !1, n = ut) {
  if (n) {
    let s = ln.get(n);
    s || ln.set(n, s = []), s.push(e);
  }
}
function zi(e, t, n = k) {
  const { immediate: s, deep: r, once: i, scheduler: o, augmentJob: l, call: c } = n, d = (A) => r ? A : /* @__PURE__ */ xe(A) || r === !1 || r === 0 ? Ye(A, 1) : Ye(A);
  let u, p, w, x, T = !1, C = !1;
  if (/* @__PURE__ */ fe(e) ? (p = () => e.value, T = /* @__PURE__ */ xe(e)) : /* @__PURE__ */ ht(e) ? (p = () => d(e), T = !0) : $(e) ? (C = !0, T = e.some((A) => /* @__PURE__ */ ht(A) || /* @__PURE__ */ xe(A)), p = () => e.map((A) => {
    if (/* @__PURE__ */ fe(A))
      return A.value;
    if (/* @__PURE__ */ ht(A))
      return d(A);
    if (F(A))
      return c ? c(A, 2) : A();
  })) : F(e) ? t ? p = c ? () => c(e, 2) : e : p = () => {
    if (w) {
      Ze();
      try {
        w();
      } finally {
        Qe();
      }
    }
    const A = ut;
    ut = u;
    try {
      return c ? c(e, 3, [x]) : e(x);
    } finally {
      ut = A;
    }
  } : p = Ke, t && r) {
    const A = p, D = r === !0 ? 1 / 0 : r;
    p = () => Ye(A(), D);
  }
  const G = xi(), L = () => {
    u.stop(), G && G.active && Zn(G.effects, u);
  };
  if (i && t) {
    const A = t;
    t = (...D) => {
      const ve = A(...D);
      return L(), ve;
    };
  }
  let R = C ? new Array(e.length).fill(Xt) : Xt;
  const H = (A) => {
    if (!(!(u.flags & 1) || !u.dirty && !A))
      if (t) {
        const D = u.run();
        if (A || r || T || (C ? D.some((ve, ge) => Ue(ve, R[ge])) : Ue(D, R))) {
          w && w();
          const ve = ut;
          ut = u;
          try {
            const ge = [
              D,
              // pass undefined as the old value when it's changed for the first time
              R === Xt ? void 0 : C && R[0] === Xt ? [] : R,
              x
            ];
            R = D, c ? c(t, 3, ge) : (
              // @ts-expect-error
              t(...ge)
            );
          } finally {
            ut = ve;
          }
        }
      } else
        u.run();
  };
  return l && l(H), u = new lr(p), u.scheduler = o ? () => o(H, !1) : H, x = (A) => qi(A, !1, u), w = u.onStop = () => {
    const A = ln.get(u);
    if (A) {
      if (c)
        c(A, 4);
      else
        for (const D of A) D();
      ln.delete(u);
    }
  }, t ? s ? H(!0) : R = u.run() : o ? o(H.bind(null, !0), !0) : u.run(), L.pause = u.pause.bind(u), L.resume = u.resume.bind(u), L.stop = L, L;
}
function Ye(e, t = 1 / 0, n) {
  if (t <= 0 || !W(e) || e.__v_skip || (n = n || /* @__PURE__ */ new Map(), (n.get(e) || 0) >= t))
    return e;
  if (n.set(e, t), t--, /* @__PURE__ */ fe(e))
    Ye(e.value, t, n);
  else if ($(e))
    for (let s = 0; s < e.length; s++)
      Ye(e[s], t, n);
  else if (on(e) || it(e))
    e.forEach((s) => {
      Ye(s, t, n);
    });
  else if (tr(e)) {
    for (const s in e)
      Ye(e[s], t, n);
    for (const s of Object.getOwnPropertySymbols(e))
      Object.prototype.propertyIsEnumerable.call(e, s) && Ye(e[s], t, n);
  }
  return e;
}
function kt(e, t, n, s) {
  try {
    return s ? e(...s) : e();
  } catch (r) {
    xn(r, t, n);
  }
}
function Pe(e, t, n, s) {
  if (F(e)) {
    const r = kt(e, t, n, s);
    return r && Qs(r) && r.catch((i) => {
      xn(i, t, n);
    }), r;
  }
  if ($(e)) {
    const r = [];
    for (let i = 0; i < e.length; i++)
      r.push(Pe(e[i], t, n, s));
    return r;
  }
}
function xn(e, t, n, s = !0) {
  const r = t ? t.vnode : null, { errorHandler: i, throwUnhandledErrorInProduction: o } = t && t.appContext.config || k;
  if (t) {
    let l = t.parent;
    const c = t.proxy, d = `https://vuejs.org/error-reference/#runtime-${n}`;
    for (; l; ) {
      const u = l.ec;
      if (u) {
        for (let p = 0; p < u.length; p++)
          if (u[p](e, c, d) === !1)
            return;
      }
      l = l.parent;
    }
    if (i) {
      Ze(), kt(i, null, 10, [
        e,
        c,
        d
      ]), Qe();
      return;
    }
  }
  Yi(e, n, r, s, o);
}
function Yi(e, t, n, s = !0, r = !1) {
  if (r)
    throw e;
  console.error(e);
}
const ue = [];
let Ne = -1;
const wt = [];
let st = null, yt = 0;
const xr = /* @__PURE__ */ Promise.resolve();
let cn = null;
function Xi(e) {
  const t = cn || xr;
  return e ? t.then(this ? e.bind(this) : e) : t;
}
function Zi(e) {
  let t = Ne + 1, n = ue.length;
  for (; t < n; ) {
    const s = t + n >>> 1, r = ue[s], i = Kt(r);
    i < e || i === e && r.flags & 2 ? t = s + 1 : n = s;
  }
  return t;
}
function us(e) {
  if (!(e.flags & 1)) {
    const t = Kt(e), n = ue[ue.length - 1];
    !n || // fast path when the job id is larger than the tail
    !(e.flags & 2) && t >= Kt(n) ? ue.push(e) : ue.splice(Zi(t), 0, e), e.flags |= 1, Sr();
  }
}
function Sr() {
  cn || (cn = xr.then(Cr));
}
function Qi(e) {
  if (!$(e))
    st && e.id === -1 ? st.splice(yt + 1, 0, e) : e.flags & 1 || (wt.push(e), e.flags |= 1);
  else
    for (let t = 0; t < e.length; t++)
      wt.push(e[t]);
  Sr();
}
function Is(e, t, n = Ne + 1) {
  for (; n < ue.length; n++) {
    const s = ue[n];
    if (s && s.flags & 2) {
      if (e && s.id !== e.uid)
        continue;
      ue.splice(n, 1), n--, s.flags & 4 && (s.flags &= -2), s(), s.flags & 4 || (s.flags &= -2);
    }
  }
}
function Er(e) {
  if (wt.length) {
    const t = [...new Set(wt)].sort(
      (n, s) => Kt(n) - Kt(s)
    );
    if (wt.length = 0, st) {
      for (let n = 0; n < t.length; n++)
        st.push(t[n]);
      return;
    }
    for (st = t, yt = 0; yt < st.length; yt++) {
      const n = st[yt];
      n.flags & 4 && (n.flags &= -2), n.flags & 8 || n(), n.flags &= -2;
    }
    st = null, yt = 0;
  }
}
const Kt = (e) => e.id == null ? e.flags & 2 ? -1 : 1 / 0 : e.id;
function Cr(e) {
  try {
    for (Ne = 0; Ne < ue.length; Ne++) {
      const t = ue[Ne];
      t && !(t.flags & 8) && (t.flags & 4 && (t.flags &= -2), kt(
        t,
        t.i,
        t.i ? 15 : 14
      ), t.flags & 4 || (t.flags &= -2));
    }
  } finally {
    for (; Ne < ue.length; Ne++) {
      const t = ue[Ne];
      t && (t.flags &= -2);
    }
    Ne = -1, ue.length = 0, Er(), cn = null, (ue.length || wt.length) && Cr();
  }
}
let we = null, Ir = null;
function fn(e) {
  const t = we;
  return we = e, Ir = e && e.type.__scopeId || null, t;
}
function eo(e, t = we, n) {
  if (!t || e._n)
    return e;
  const s = (...r) => {
    s._d && Ds(-1);
    const i = fn(t), o = pt.length;
    let l;
    try {
      l = e(...r);
    } finally {
      for (let c = pt.length; c > o; c--) Qr();
      fn(i), s._d && Ds(1);
    }
    return l;
  };
  return s._n = !0, s._c = !0, s._d = !0, s;
}
function Tr(e, t) {
  if (we === null)
    return e;
  const n = Tn(we), s = e.dirs || (e.dirs = []);
  for (let r = 0; r < t.length; r++) {
    let [i, o, l, c = k] = t[r];
    i && (F(i) && (i = {
      mounted: i,
      updated: i
    }), i.deep && Ye(o), s.push({
      dir: i,
      instance: n,
      value: o,
      oldValue: void 0,
      arg: l,
      modifiers: c
    }));
  }
  return e;
}
function ft(e, t, n, s) {
  const r = e.dirs, i = t && t.dirs;
  for (let o = 0; o < r.length; o++) {
    const l = r[o];
    i && (l.oldValue = i[o].value);
    let c = l.dir[s];
    c && (Ze(), Pe(c, n, 8, [
      e.el,
      l,
      e,
      t
    ]), Qe());
  }
}
function to(e, t) {
  if (de) {
    let n = de.provides;
    const s = de.parent && de.parent.provides;
    s === n && (n = de.provides = Object.create(s)), n[e] = t;
  }
}
function nn(e, t, n = !1) {
  const s = Qo();
  if (s || xt) {
    let r = xt ? xt._context.provides : s ? s.parent == null || s.ce ? s.vnode.appContext && s.vnode.appContext.provides : s.parent.provides : void 0;
    if (r && e in r)
      return r[e];
    if (arguments.length > 1)
      return n && F(t) ? t.call(s && s.proxy) : t;
  }
}
const no = /* @__PURE__ */ Symbol.for("v-scx"), so = () => nn(no);
function sn(e, t, n) {
  return Ar(e, t, n);
}
function Ar(e, t, n = k) {
  const { immediate: s, deep: r, flush: i, once: o } = n, l = oe({}, n), c = t && s || !t && i !== "post";
  let d;
  if (Wt) {
    if (i === "sync") {
      const x = so();
      d = x.__watcherHandles || (x.__watcherHandles = []);
    } else if (!c) {
      const x = () => {
      };
      return x.stop = Ke, x.resume = Ke, x.pause = Ke, x;
    }
  }
  const u = de;
  l.call = (x, T, C) => Pe(x, u, T, C);
  let p = !1;
  i === "post" ? l.scheduler = (x) => {
    pe(x, u && u.suspense);
  } : i !== "sync" && (p = !0, l.scheduler = (x, T) => {
    T ? x() : us(x);
  }), l.augmentJob = (x) => {
    t && (x.flags |= 4), p && (x.flags |= 2, u && (x.id = u.uid, x.i = u));
  };
  const w = zi(e, t, l);
  return Wt && (d ? d.push(w) : c && w()), w;
}
function ro(e, t, n) {
  const s = this.proxy, r = Y(e) ? e.includes(".") ? Or(s, e) : () => s[e] : e.bind(s, s);
  let i;
  F(t) ? i = t : (i = t.handler, n = t);
  const o = Gt(this), l = Ar(r, i.bind(s), n);
  return o(), l;
}
function Or(e, t) {
  const n = t.split(".");
  return () => {
    let s = e;
    for (let r = 0; r < n.length && s; r++)
      s = s[n[r]];
    return s;
  };
}
const io = /* @__PURE__ */ Symbol("_vte"), Sn = (e) => e.__isTeleport, Rn = /* @__PURE__ */ Symbol("_leaveCb");
function oo(e) {
  let t = e[0];
  if (e.length > 1) {
    for (const n of e)
      if (n.type !== tt) {
        t = n;
        break;
      }
  }
  return t;
}
function Pr(e) {
  if (!hs(e))
    return Sn(e.type) && e.children ? oo(e.children) : e;
  if (e.component)
    return e.component.subTree;
  const { shapeFlag: t, children: n } = e;
  if (n) {
    if (t & 16)
      return n[0];
    if (t & 32 && F(n.default))
      return n.default();
  }
}
function ds(e, t) {
  if (e.shapeFlag & 6 && e.component) {
    e.transition = t;
    const n = e.component.subTree;
    ds(
      Sn(n.type) && Pr(n) || n,
      t
    );
  } else e.shapeFlag & 128 ? (e.ssContent.transition = t.clone(e.ssContent), e.ssFallback.transition = t.clone(e.ssFallback)) : e.transition = t;
}
// @__NO_SIDE_EFFECTS__
function Mr(e, t) {
  return F(e) ? (
    // #8236: extend call and options.name access are considered side-effects
    // by Rollup, so we have to wrap it in a pure-annotated IIFE.
    oe({ name: e.name }, t, { setup: e })
  ) : e;
}
function $r(e) {
  e.ids = [e.ids[0] + e.ids[2]++ + "-", 0, 0];
}
function Ts(e, t) {
  let n;
  return !!((n = Object.getOwnPropertyDescriptor(e, t)) && !n.configurable);
}
const an = /* @__PURE__ */ new WeakMap();
function Nt(e, t, n, s, r = !1) {
  if ($(e)) {
    e.forEach(
      (C, G) => Nt(
        C,
        t && ($(t) ? t[G] : t),
        n,
        s,
        r
      )
    );
    return;
  }
  if (Dt(s) && !r) {
    s.shapeFlag & 512 && s.type.__asyncResolved && s.component.subTree.component && Nt(e, t, n, s.component.subTree);
    return;
  }
  const i = s.shapeFlag & 4 ? Tn(s.component) : s.el, o = r ? null : i, { i: l, r: c } = e, d = t && t.r, u = l.refs === k ? l.refs = {} : l.refs, p = l.setupState, w = /* @__PURE__ */ U(p), x = p === k ? Zs : (C) => Ts(u, C) ? !1 : K(w, C), T = (C, G) => !(G && Ts(u, G));
  if (d != null && d !== c) {
    if (As(t), Y(d))
      u[d] = null, x(d) && (p[d] = null);
    else if (/* @__PURE__ */ fe(d)) {
      const C = t;
      T(d, C.k) && (d.value = null), C.k && (u[C.k] = null);
    }
  }
  if (F(c))
    kt(c, l, 12, [o, u]);
  else {
    const C = Y(c), G = /* @__PURE__ */ fe(c);
    if (C || G) {
      const L = () => {
        if (e.f) {
          const R = C ? x(c) ? p[c] : u[c] : T() || !e.k ? c.value : u[e.k];
          if (r)
            $(R) && Zn(R, i);
          else if ($(R))
            R.includes(i) || R.push(i);
          else if (C)
            u[c] = [i], x(c) && (p[c] = u[c]);
          else {
            const H = [i];
            T(c, e.k) && (c.value = H), e.k && (u[e.k] = H);
          }
        } else C ? (u[c] = o, x(c) && (p[c] = o)) : G && (T(c, e.k) && (c.value = o), e.k && (u[e.k] = o));
      };
      if (o) {
        const R = () => {
          L(), an.delete(e);
        };
        R.id = -1, an.set(e, R), pe(R, n);
      } else
        As(e), L();
    }
  }
}
function As(e) {
  const t = an.get(e);
  t && (t.flags |= 8, an.delete(e));
}
vn().requestIdleCallback;
vn().cancelIdleCallback;
const Dt = (e) => !!e.type.__asyncLoader, hs = (e) => e.type.__isKeepAlive;
function lo(e, t) {
  Rr(e, "a", t);
}
function co(e, t) {
  Rr(e, "da", t);
}
function Rr(e, t, n = de) {
  const s = e.__wdc || (e.__wdc = () => {
    let r = n;
    for (; r; ) {
      if (r.isDeactivated)
        return;
      r = r.parent;
    }
    return e();
  });
  if (En(t, s, n), n) {
    let r = n.parent;
    for (; r && r.parent; )
      hs(r.parent.vnode) && fo(s, t, n, r), r = r.parent;
  }
}
function fo(e, t, n, s) {
  const r = En(
    t,
    e,
    s,
    !0
    /* prepend */
  );
  jr(() => {
    Zn(s[t], r);
  }, n);
}
function En(e, t, n = de, s = !1) {
  if (n) {
    const r = n[e] || (n[e] = []), i = t.__weh || (t.__weh = (...o) => {
      Ze();
      const l = Gt(n), c = Pe(t, n, e, o);
      return l(), Qe(), c;
    });
    return s ? r.unshift(i) : r.push(i), i;
  }
}
const nt = (e) => (t, n = de) => {
  (!Wt || e === "sp") && En(e, (...s) => t(...s), n);
}, ao = nt("bm"), ps = nt("m"), uo = nt(
  "bu"
), ho = nt("u"), Fr = nt(
  "bum"
), jr = nt("um"), po = nt(
  "sp"
), go = nt("rtg"), mo = nt("rtc");
function _o(e, t = de) {
  En("ec", e, t);
}
const yo = /* @__PURE__ */ Symbol.for("v-ndc");
function vo(e, t, n, s) {
  let r;
  const i = n, o = $(e);
  if (o || Y(e)) {
    const l = o && /* @__PURE__ */ ht(e);
    let c = !1, d = !1;
    l && (c = !/* @__PURE__ */ xe(e), d = /* @__PURE__ */ et(e), e = wn(e)), r = new Array(e.length);
    for (let u = 0, p = e.length; u < p; u++)
      r[u] = t(
        c ? d ? St(Oe(e[u])) : Oe(e[u]) : e[u],
        u,
        void 0,
        i
      );
  } else if (typeof e == "number") {
    r = new Array(e);
    for (let l = 0; l < e; l++)
      r[l] = t(l + 1, l, void 0, i);
  } else if (W(e))
    if (e[Symbol.iterator])
      r = Array.from(
        e,
        (l, c) => t(l, c, void 0, i)
      );
    else {
      const l = Object.keys(e);
      r = new Array(l.length);
      for (let c = 0, d = l.length; c < d; c++) {
        const u = l[c];
        r[c] = t(e[u], u, c, i);
      }
    }
  else
    r = [];
  return r;
}
const kn = (e) => e ? ri(e) ? Tn(e) : kn(e.parent) : null, Lt = (
  // Move PURE marker to new line to workaround compiler discarding it
  // due to type annotation
  /* @__PURE__ */ oe(/* @__PURE__ */ Object.create(null), {
    $: (e) => e,
    $el: (e) => e.vnode.el,
    $data: (e) => e.data,
    $props: (e) => e.props,
    $attrs: (e) => e.attrs,
    $slots: (e) => e.slots,
    $refs: (e) => e.refs,
    $parent: (e) => kn(e.parent),
    $root: (e) => kn(e.root),
    $host: (e) => e.ce,
    $emit: (e) => e.emit,
    $options: (e) => Dr(e),
    $forceUpdate: (e) => e.f || (e.f = () => {
      us(e.update);
    }),
    $nextTick: (e) => e.n || (e.n = Xi.bind(e.proxy)),
    $watch: (e) => ro.bind(e)
  })
), Fn = (e, t) => e !== k && !e.__isScriptSetup && K(e, t), bo = {
  get({ _: e }, t) {
    if (t === "__v_skip")
      return !0;
    const { ctx: n, setupState: s, data: r, props: i, accessCache: o, type: l, appContext: c } = e;
    if (t[0] !== "$") {
      const w = o[t];
      if (w !== void 0)
        switch (w) {
          case 1:
            return s[t];
          case 2:
            return r[t];
          case 4:
            return n[t];
          case 3:
            return i[t];
        }
      else {
        if (Fn(s, t))
          return o[t] = 1, s[t];
        if (r !== k && K(r, t))
          return o[t] = 2, r[t];
        if (K(i, t))
          return o[t] = 3, i[t];
        if (n !== k && K(n, t))
          return o[t] = 4, n[t];
        Gn && (o[t] = 0);
      }
    }
    const d = Lt[t];
    let u, p;
    if (d)
      return t === "$attrs" && ce(e.attrs, "get", ""), d(e);
    if (
      // css module (injected by vue-loader)
      (u = l.__cssModules) && (u = u[t])
    )
      return u;
    if (n !== k && K(n, t))
      return o[t] = 4, n[t];
    if (
      // global properties
      p = c.config.globalProperties, K(p, t)
    )
      return p[t];
  },
  set({ _: e }, t, n) {
    const { data: s, setupState: r, ctx: i } = e;
    return Fn(r, t) ? (r[t] = n, !0) : s !== k && K(s, t) ? (s[t] = n, !0) : K(e.props, t) || t[0] === "$" && t.slice(1) in e ? !1 : (i[t] = n, !0);
  },
  has({
    _: { data: e, setupState: t, accessCache: n, ctx: s, appContext: r, props: i, type: o }
  }, l) {
    let c;
    return !!(n[l] || e !== k && l[0] !== "$" && K(e, l) || Fn(t, l) || K(i, l) || K(s, l) || K(Lt, l) || K(r.config.globalProperties, l) || (c = o.__cssModules) && c[l]);
  },
  defineProperty(e, t, n) {
    return n.get != null ? e._.accessCache[t] = 0 : K(n, "value") && this.set(e, t, n.value, null), Reflect.defineProperty(e, t, n);
  }
};
function Os(e) {
  return $(e) ? e.reduce(
    (t, n) => (t[n] = null, t),
    {}
  ) : e;
}
let Gn = !0;
function wo(e) {
  const t = Dr(e), n = e.proxy, s = e.ctx;
  Gn = !1, t.beforeCreate && Ps(t.beforeCreate, e, "bc");
  const {
    // state
    data: r,
    computed: i,
    methods: o,
    watch: l,
    provide: c,
    inject: d,
    // lifecycle
    created: u,
    beforeMount: p,
    mounted: w,
    beforeUpdate: x,
    updated: T,
    activated: C,
    deactivated: G,
    beforeDestroy: L,
    beforeUnmount: R,
    destroyed: H,
    unmounted: A,
    render: D,
    renderTracked: ve,
    renderTriggered: ge,
    errorCaptured: he,
    serverPrefetch: Je,
    // public API
    expose: Se,
    inheritAttrs: lt,
    // assets
    components: ct,
    directives: ee,
    filters: gt
  } = t;
  if (d && xo(d, s, null), o)
    for (const z in o) {
      const V = o[z];
      F(V) && (s[z] = V.bind(n));
    }
  if (r) {
    const z = r.call(n, n);
    W(z) && (e.data = /* @__PURE__ */ cs(z));
  }
  if (Gn = !0, i)
    for (const z in i) {
      const V = i[z], We = F(V) ? V.bind(n, n) : F(V.get) ? V.get.bind(n, n) : Ke, Ee = !F(V) && F(V.set) ? V.set.bind(n) : Ke, Be = rt({
        get: We,
        set: Ee
      });
      Object.defineProperty(s, z, {
        enumerable: !0,
        configurable: !0,
        get: () => Be.value,
        set: (E) => Be.value = E
      });
    }
  if (l)
    for (const z in l)
      Nr(l[z], s, n, z);
  if (c) {
    const z = F(c) ? c.call(n) : c;
    Reflect.ownKeys(z).forEach((V) => {
      to(V, z[V]);
    });
  }
  u && Ps(u, e, "c");
  function te(z, V) {
    $(V) ? V.forEach((We) => z(We.bind(n))) : V && z(V.bind(n));
  }
  if (te(ao, p), te(ps, w), te(uo, x), te(ho, T), te(lo, C), te(co, G), te(_o, he), te(mo, ve), te(go, ge), te(Fr, R), te(jr, A), te(po, Je), $(Se))
    if (Se.length) {
      const z = e.exposed || (e.exposed = {});
      Se.forEach((V) => {
        Object.defineProperty(z, V, {
          get: () => n[V],
          set: (We) => n[V] = We,
          enumerable: !0
        });
      });
    } else e.exposed || (e.exposed = {});
  D && e.render === Ke && (e.render = D), lt != null && (e.inheritAttrs = lt), ct && (e.components = ct), ee && (e.directives = ee), Je && $r(e);
}
function xo(e, t, n = Ke) {
  $(e) && (e = qn(e));
  for (const s in e) {
    const r = e[s];
    let i;
    W(r) ? "default" in r ? i = nn(
      r.from || s,
      r.default,
      !0
    ) : i = nn(r.from || s) : i = nn(r), /* @__PURE__ */ fe(i) ? Object.defineProperty(t, s, {
      enumerable: !0,
      configurable: !0,
      get: () => i.value,
      set: (o) => i.value = o
    }) : t[s] = i;
  }
}
function Ps(e, t, n) {
  Pe(
    $(e) ? e.map((s) => s.bind(t.proxy)) : e.bind(t.proxy),
    t,
    n
  );
}
function Nr(e, t, n, s) {
  let r = s.includes(".") ? Or(n, s) : () => n[s];
  if (Y(e)) {
    const i = t[e];
    F(i) && sn(r, i);
  } else if (F(e))
    sn(r, e.bind(n));
  else if (W(e))
    if ($(e))
      e.forEach((i) => Nr(i, t, n, s));
    else {
      const i = F(e.handler) ? e.handler.bind(n) : t[e.handler];
      F(i) && sn(r, i, e);
    }
}
function Dr(e) {
  const t = e.type, { mixins: n, extends: s } = t, {
    mixins: r,
    optionsCache: i,
    config: { optionMergeStrategies: o }
  } = e.appContext, l = i.get(t);
  let c;
  return l ? c = l : !r.length && !n && !s ? c = t : (c = {}, r.length && r.forEach(
    (d) => un(c, d, o, !0)
  ), un(c, t, o)), W(t) && i.set(t, c), c;
}
function un(e, t, n, s = !1) {
  const { mixins: r, extends: i } = t;
  i && un(e, i, n, !0), r && r.forEach(
    (o) => un(e, o, n, !0)
  );
  for (const o in t)
    if (!(s && o === "expose")) {
      const l = So[o] || n && n[o];
      e[o] = l ? l(e[o], t[o]) : t[o];
    }
  return e;
}
const So = {
  data: Ms,
  props: $s,
  emits: $s,
  // objects
  methods: Pt,
  computed: Pt,
  // lifecycle
  beforeCreate: ae,
  created: ae,
  beforeMount: ae,
  mounted: ae,
  beforeUpdate: ae,
  updated: ae,
  beforeDestroy: ae,
  beforeUnmount: ae,
  destroyed: ae,
  unmounted: ae,
  activated: ae,
  deactivated: ae,
  errorCaptured: ae,
  serverPrefetch: ae,
  // assets
  components: Pt,
  directives: Pt,
  // watch
  watch: Co,
  // provide / inject
  provide: Ms,
  inject: Eo
};
function Ms(e, t) {
  return t ? e ? function() {
    return oe(
      F(e) ? e.call(this, this) : e,
      F(t) ? t.call(this, this) : t
    );
  } : t : e;
}
function Eo(e, t) {
  return Pt(qn(e), qn(t));
}
function qn(e) {
  if ($(e)) {
    const t = {};
    for (let n = 0; n < e.length; n++)
      t[e[n]] = e[n];
    return t;
  }
  return e;
}
function ae(e, t) {
  return e ? [...new Set([].concat(e, t))] : t;
}
function Pt(e, t) {
  return e ? oe(/* @__PURE__ */ Object.create(null), e, t) : t;
}
function $s(e, t) {
  return e ? $(e) && $(t) ? [.../* @__PURE__ */ new Set([...e, ...t])] : oe(
    /* @__PURE__ */ Object.create(null),
    Os(e),
    Os(t ?? {})
  ) : t;
}
function Co(e, t) {
  if (!e) return t;
  if (!t) return e;
  const n = oe(/* @__PURE__ */ Object.create(null), e);
  for (const s in t)
    n[s] = ae(e[s], t[s]);
  return n;
}
function Lr() {
  return {
    app: null,
    config: {
      isNativeTag: Zs,
      performance: !1,
      globalProperties: {},
      optionMergeStrategies: {},
      errorHandler: void 0,
      warnHandler: void 0,
      compilerOptions: {}
    },
    mixins: [],
    components: {},
    directives: {},
    provides: /* @__PURE__ */ Object.create(null),
    optionsCache: /* @__PURE__ */ new WeakMap(),
    propsCache: /* @__PURE__ */ new WeakMap(),
    emitsCache: /* @__PURE__ */ new WeakMap()
  };
}
let Io = 0;
function To(e, t) {
  return function(s, r = null) {
    F(s) || (s = oe({}, s)), r != null && !W(r) && (r = null);
    const i = Lr(), o = /* @__PURE__ */ new WeakSet(), l = [];
    let c = !1;
    const d = i.app = {
      _uid: Io++,
      _component: s,
      _props: r,
      _container: null,
      _context: i,
      _instance: null,
      version: il,
      get config() {
        return i.config;
      },
      set config(u) {
      },
      use(u, ...p) {
        return o.has(u) || (u && F(u.install) ? (o.add(u), u.install(d, ...p)) : F(u) && (o.add(u), u(d, ...p))), d;
      },
      mixin(u) {
        return i.mixins.includes(u) || i.mixins.push(u), d;
      },
      component(u, p) {
        return p ? (i.components[u] = p, d) : i.components[u];
      },
      directive(u, p) {
        return p ? (i.directives[u] = p, d) : i.directives[u];
      },
      mount(u, p, w) {
        if (!c) {
          const x = d._ceVNode || Xe(s, r);
          return x.appContext = i, w === !0 ? w = "svg" : w === !1 && (w = void 0), e(x, u, w), c = !0, d._container = u, u.__vue_app__ = d, Tn(x.component);
        }
      },
      onUnmount(u) {
        l.push(u);
      },
      unmount() {
        c && (Pe(
          l,
          d._instance,
          16
        ), e(null, d._container), delete d._container.__vue_app__);
      },
      provide(u, p) {
        return i.provides[u] = p, d;
      },
      runWithContext(u) {
        const p = xt;
        xt = d;
        try {
          return u();
        } finally {
          xt = p;
        }
      }
    };
    return d;
  };
}
let xt = null;
const Ao = (e, t) => t === "modelValue" || t === "model-value" ? e.modelModifiers : e[`${t}Modifiers`] || e[`${Te(t)}Modifiers`] || e[`${ot(t)}Modifiers`];
function Oo(e, t, ...n) {
  if (e.isUnmounted) return;
  const s = e.vnode.props || k;
  let r = n;
  const i = t.startsWith("update:"), o = i && Ao(s, t.slice(7));
  o && (o.trim && (r = n.map((u) => Y(u) ? u.trim() : u)), o.number && (r = r.map(es)));
  let l, c = s[l = An(t)] || // also try camelCase event handler (#2249)
  s[l = An(Te(t))];
  !c && i && (c = s[l = An(ot(t))]), c && Pe(
    c,
    e,
    6,
    r
  );
  const d = s[l + "Once"];
  if (d) {
    if (!e.emitted)
      e.emitted = {};
    else if (e.emitted[l])
      return;
    e.emitted[l] = !0, Pe(
      d,
      e,
      6,
      r
    );
  }
}
const Po = /* @__PURE__ */ new WeakMap();
function Hr(e, t, n = !1) {
  const s = n ? Po : t.emitsCache, r = s.get(e);
  if (r !== void 0)
    return r;
  const i = e.emits;
  let o = {}, l = !1;
  if (!F(e)) {
    const c = (d) => {
      const u = Hr(d, t, !0);
      u && (l = !0, oe(o, u));
    };
    !n && t.mixins.length && t.mixins.forEach(c), e.extends && c(e.extends), e.mixins && e.mixins.forEach(c);
  }
  return !i && !l ? (W(e) && s.set(e, null), null) : ($(i) ? i.forEach((c) => o[c] = null) : oe(o, i), W(e) && s.set(e, o), o);
}
function Cn(e, t) {
  return !e || !mn(t) ? !1 : (t = t.slice(2), t = t === "Once" ? t : t.replace(/Once$/, ""), K(e, t[0].toLowerCase() + t.slice(1)) || K(e, ot(t)) || K(e, t));
}
function Rs(e) {
  const {
    type: t,
    vnode: n,
    proxy: s,
    withProxy: r,
    propsOptions: [i],
    slots: o,
    attrs: l,
    emit: c,
    render: d,
    renderCache: u,
    props: p,
    data: w,
    setupState: x,
    ctx: T,
    inheritAttrs: C
  } = e, G = fn(e);
  let L, R;
  try {
    if (n.shapeFlag & 4) {
      const A = r || s, D = A;
      L = He(
        d.call(
          D,
          A,
          u,
          p,
          x,
          w,
          T
        )
      ), R = l;
    } else {
      const A = t;
      L = He(
        A.length > 1 ? A(
          p,
          { attrs: l, slots: o, emit: c }
        ) : A(
          p,
          null
        )
      ), R = t.props ? l : Mo(l);
    }
  } catch (A) {
    pt.length = 0, xn(A, e, 1), L = Xe(tt);
  }
  let H = L;
  if (R && C !== !1) {
    const A = Object.keys(R), { shapeFlag: D } = H;
    A.length && D & 7 && (i && A.some(_n) && (R = $o(
      R,
      i
    )), H = Et(H, R, !1, !0));
  }
  if (n.dirs && (H = Et(H, null, !1, !0), H.dirs = H.dirs ? H.dirs.concat(n.dirs) : n.dirs), n.transition) {
    const A = Sn(H.type) && Pr(H) || H;
    ds(A, n.transition);
  }
  return L = H, fn(G), L;
}
const Mo = (e) => {
  let t;
  for (const n in e)
    (n === "class" || n === "style" || mn(n)) && ((t || (t = {}))[n] = e[n]);
  return t;
}, $o = (e, t) => {
  const n = {};
  for (const s in e)
    (!_n(s) || !(s.slice(9) in t)) && (n[s] = e[s]);
  return n;
};
function Ro(e, t, n) {
  const { props: s, children: r, component: i } = e, { props: o, children: l, patchFlag: c } = t, d = i.emitsOptions;
  if (t.dirs || t.transition)
    return !0;
  if (n && c >= 0) {
    if (c & 1024)
      return !0;
    if (c & 16)
      return s ? Fs(s, o, d) : !!o;
    if (c & 8) {
      const u = t.dynamicProps;
      for (let p = 0; p < u.length; p++) {
        const w = u[p];
        if (Ur(o, s, w) && !Cn(d, w))
          return !0;
      }
    }
  } else
    return (r || l) && (!l || !l.$stable) ? !0 : s === o ? !1 : s ? o ? Fs(s, o, d) : !0 : !!o;
  return !1;
}
function Fs(e, t, n) {
  const s = Object.keys(t);
  if (s.length !== Object.keys(e).length)
    return !0;
  for (let r = 0; r < s.length; r++) {
    const i = s[r];
    if (Ur(t, e, i) && !Cn(n, i))
      return !0;
  }
  return !1;
}
function Ur(e, t, n) {
  const s = e[n], r = t[n];
  return n === "style" && W(s) && W(r) ? !bn(s, r) : s !== r;
}
function Fo({ vnode: e, parent: t, suspense: n }, s) {
  for (; t; ) {
    const r = t.subTree;
    if (r.suspense && r.suspense.activeBranch === e && (r.suspense.vnode.el = r.el = s, e = r), r === e)
      (e = t.vnode).el = s, t = t.parent;
    else
      break;
  }
  n && n.activeBranch === e && (n.vnode.el = s);
}
const Kr = {}, Vr = () => Object.create(Kr), Jr = (e) => Object.getPrototypeOf(e) === Kr;
function jo(e, t, n, s = !1) {
  const r = {}, i = Vr();
  e.propsDefaults = /* @__PURE__ */ Object.create(null), Wr(e, t, r, i);
  for (const o in e.propsOptions[0])
    o in r || (r[o] = void 0);
  n ? e.props = s ? r : /* @__PURE__ */ Ui(r) : e.type.props ? e.props = r : e.props = i, e.attrs = i;
}
function No(e, t, n, s) {
  const {
    props: r,
    attrs: i,
    vnode: { patchFlag: o }
  } = e, l = /* @__PURE__ */ U(r), [c] = e.propsOptions;
  let d = !1;
  if (
    // always force full diff in dev
    // - #1942 if hmr is enabled with sfc component
    // - vite#872 non-sfc component used by sfc component
    (s || o > 0) && !(o & 16)
  ) {
    if (o & 8) {
      const u = e.vnode.dynamicProps;
      for (let p = 0; p < u.length; p++) {
        let w = u[p];
        if (Cn(e.emitsOptions, w))
          continue;
        const x = t[w];
        if (c)
          if (K(i, w))
            x !== i[w] && (i[w] = x, d = !0);
          else {
            const T = Te(w);
            r[T] = zn(
              c,
              l,
              T,
              x,
              e,
              !1
            );
          }
        else
          x !== i[w] && (i[w] = x, d = !0);
      }
    }
  } else {
    Wr(e, t, r, i) && (d = !0);
    let u;
    for (const p in l)
      (!t || // for camelCase
      !K(t, p) && // it's possible the original props was passed in as kebab-case
      // and converted to camelCase (#955)
      ((u = ot(p)) === p || !K(t, u))) && (c ? n && // for camelCase
      (n[p] !== void 0 || // for kebab-case
      n[u] !== void 0) && (r[p] = zn(
        c,
        l,
        p,
        void 0,
        e,
        !0
      )) : delete r[p]);
    if (i !== l)
      for (const p in i)
        (!t || !K(t, p)) && (delete i[p], d = !0);
  }
  d && ze(e.attrs, "set", "");
}
function Wr(e, t, n, s) {
  const [r, i] = e.propsOptions;
  let o = !1, l;
  if (t)
    for (let c in t) {
      if (Rt(c))
        continue;
      const d = t[c];
      let u;
      r && K(r, u = Te(c)) ? !i || !i.includes(u) ? n[u] = d : (l || (l = {}))[u] = d : Cn(e.emitsOptions, c) || (!(c in s) || d !== s[c]) && (s[c] = d, o = !0);
    }
  if (i) {
    const c = /* @__PURE__ */ U(n), d = l || k;
    for (let u = 0; u < i.length; u++) {
      const p = i[u];
      n[p] = zn(
        r,
        c,
        p,
        d[p],
        e,
        !K(d, p)
      );
    }
  }
  return o;
}
function zn(e, t, n, s, r, i) {
  const o = e[n];
  if (o != null) {
    const l = K(o, "default");
    if (l && s === void 0) {
      const c = o.default;
      if (o.type !== Function && !o.skipFactory && F(c)) {
        const { propsDefaults: d } = r;
        if (n in d)
          s = d[n];
        else {
          const u = Gt(r);
          s = d[n] = c.call(
            null,
            t
          ), u();
        }
      } else
        s = c;
      r.ce && r.ce._setProp(n, s);
    }
    o[
      0
      /* shouldCast */
    ] && (i && !l ? s = !1 : o[
      1
      /* shouldCastTrue */
    ] && (s === "" || s === ot(n)) && (s = !0));
  }
  return s;
}
const Do = /* @__PURE__ */ new WeakMap();
function Br(e, t, n = !1) {
  const s = n ? Do : t.propsCache, r = s.get(e);
  if (r)
    return r;
  const i = e.props, o = {}, l = [];
  let c = !1;
  if (!F(e)) {
    const u = (p) => {
      c = !0;
      const [w, x] = Br(p, t, !0);
      oe(o, w), x && l.push(...x);
    };
    !n && t.mixins.length && t.mixins.forEach(u), e.extends && u(e.extends), e.mixins && e.mixins.forEach(u);
  }
  if (!i && !c)
    return W(e) && s.set(e, bt), bt;
  if ($(i))
    for (let u = 0; u < i.length; u++) {
      const p = Te(i[u]);
      js(p) && (o[p] = k);
    }
  else if (i)
    for (const u in i) {
      const p = Te(u);
      if (js(p)) {
        const w = i[u], x = o[p] = $(w) || F(w) ? { type: w } : oe({}, w), T = x.type;
        let C = !1, G = !0;
        if ($(T))
          for (let L = 0; L < T.length; ++L) {
            const R = T[L], H = F(R) && R.name;
            if (H === "Boolean") {
              C = !0;
              break;
            } else H === "String" && (G = !1);
          }
        else
          C = F(T) && T.name === "Boolean";
        x[
          0
          /* shouldCast */
        ] = C, x[
          1
          /* shouldCastTrue */
        ] = G, (C || K(x, "default")) && l.push(p);
      }
    }
  const d = [o, l];
  return W(e) && s.set(e, d), d;
}
function js(e) {
  return e[0] !== "$" && !Rt(e);
}
const gs = (e) => e === "_" || e === "_ctx" || e === "$stable", ms = (e) => $(e) ? e.map(He) : [He(e)], Lo = (e, t, n) => {
  if (t._n)
    return t;
  const s = eo((...r) => ms(t(...r)), n);
  return s._c = !1, s;
}, kr = (e, t, n) => {
  const s = e._ctx;
  for (const r in e) {
    if (gs(r)) continue;
    const i = e[r];
    if (F(i))
      t[r] = Lo(r, i, s);
    else if (i != null) {
      const o = ms(i);
      t[r] = () => o;
    }
  }
}, Gr = (e, t) => {
  const n = ms(t);
  e.slots.default = () => n;
}, qr = (e, t, n) => {
  for (const s in t)
    (n || !gs(s)) && (e[s] = t[s]);
}, Ho = (e, t, n) => {
  const s = e.slots = Vr();
  if (e.vnode.shapeFlag & 32) {
    const r = t._;
    r ? (qr(s, t, n), n && sr(s, "_", r, !0)) : kr(t, s);
  } else t && Gr(e, t);
}, Uo = (e, t, n) => {
  const { vnode: s, slots: r } = e;
  let i = !0, o = k;
  if (s.shapeFlag & 32) {
    const l = t._;
    l ? n && l === 1 ? i = !1 : qr(r, t, n) : (i = !t.$stable, kr(t, r)), o = t;
  } else t && (Gr(e, t), o = { default: 1 });
  if (i)
    for (const l in r)
      !gs(l) && o[l] == null && delete r[l];
}, pe = Bo;
function Ko(e) {
  return Vo(e);
}
function Vo(e, t) {
  const n = vn();
  n.__VUE__ = !0;
  const {
    insert: s,
    remove: r,
    patchProp: i,
    createElement: o,
    createText: l,
    createComment: c,
    setText: d,
    setElementText: u,
    parentNode: p,
    nextSibling: w,
    setScopeId: x = Ke,
    insertStaticContent: T
  } = e, C = (f, a, h, y = null, _ = null, g = null, S = void 0, b = null, v = !!a.dynamicChildren) => {
    if (f === a)
      return;
    f && !At(f, a) && (y = Me(f), E(f, _, g, !0), f = null), a.patchFlag === -2 && (v = !1, a.dynamicChildren = null);
    const { type: m, ref: P, shapeFlag: I } = a;
    switch (m) {
      case In:
        G(f, a, h, y);
        break;
      case tt:
        L(f, a, h, y);
        break;
      case Nn:
        f == null && R(a, h, y, S);
        break;
      case Le:
        ct(
          f,
          a,
          h,
          y,
          _,
          g,
          S,
          b,
          v
        );
        break;
      default:
        I & 1 ? D(
          f,
          a,
          h,
          y,
          _,
          g,
          S,
          b,
          v
        ) : I & 6 ? ee(
          f,
          a,
          h,
          y,
          _,
          g,
          S,
          b,
          v
        ) : (I & 64 || I & 128) && m.process(
          f,
          a,
          h,
          y,
          _,
          g,
          S,
          b,
          v,
          Ct
        );
    }
    P != null && _ ? Nt(P, f && f.ref, g, a || f, !a) : P == null && f && f.ref != null && Nt(f.ref, null, g, f, !0);
  }, G = (f, a, h, y) => {
    if (f == null)
      s(
        a.el = l(a.children),
        h,
        y
      );
    else {
      const _ = a.el = f.el;
      a.children !== f.children && d(_, a.children);
    }
  }, L = (f, a, h, y) => {
    f == null ? s(
      a.el = c(a.children || ""),
      h,
      y
    ) : a.el = f.el;
  }, R = (f, a, h, y) => {
    [f.el, f.anchor] = T(
      f.children,
      a,
      h,
      y,
      f.el,
      f.anchor
    );
  }, H = ({ el: f, anchor: a }, h, y) => {
    let _;
    for (; f && f !== a; )
      _ = w(f), s(f, h, y), f = _;
    s(a, h, y);
  }, A = ({ el: f, anchor: a }) => {
    let h;
    for (; f && f !== a; )
      h = w(f), r(f), f = h;
    r(a);
  }, D = (f, a, h, y, _, g, S, b, v) => {
    if (a.type === "svg" ? S = "svg" : a.type === "math" && (S = "mathml"), f == null)
      ve(
        a,
        h,
        y,
        _,
        g,
        S,
        b,
        v
      );
    else {
      const m = f.el && f.el._isVueCE ? f.el : null;
      try {
        m && m._beginPatch(), Je(
          f,
          a,
          _,
          g,
          S,
          b,
          v
        );
      } finally {
        m && m._endPatch();
      }
    }
  }, ve = (f, a, h, y, _, g, S, b) => {
    let v, m;
    const { props: P, shapeFlag: I, transition: O, dirs: M } = f;
    if (v = f.el = o(
      f.type,
      g,
      P && P.is,
      P
    ), I & 8 ? u(v, f.children) : I & 16 && he(
      f.children,
      v,
      null,
      y,
      _,
      jn(f, g),
      S,
      b
    ), M && ft(f, null, y, "created"), ge(v, f, f.scopeId, S, y), P) {
      for (const B in P)
        B !== "value" && !Rt(B) && i(v, B, null, P[B], g, y);
      "value" in P && i(v, "value", null, P.value, g), (m = P.onVnodeBeforeMount) && je(m, y, f);
    }
    M && ft(f, null, y, "beforeMount");
    const N = Jo(_, O);
    N && O.beforeEnter(v), s(v, a, h), ((m = P && P.onVnodeMounted) || N || M) && pe(() => {
      m && je(m, y, f), N && O.enter(v), M && ft(f, null, y, "mounted");
    }, _);
  }, ge = (f, a, h, y, _) => {
    if (h && x(f, h), y)
      for (let g = 0; g < y.length; g++)
        x(f, y[g]);
    if (_) {
      let g = _.subTree;
      if (a === g || Zr(g.type) && (g.ssContent === a || g.ssFallback === a)) {
        const S = _.vnode;
        ge(
          f,
          S,
          S.scopeId,
          S.slotScopeIds,
          _.parent
        );
      }
    }
  }, he = (f, a, h, y, _, g, S, b, v = 0) => {
    for (let m = v; m < f.length; m++) {
      const P = f[m] = b ? qe(f[m]) : He(f[m]);
      C(
        null,
        P,
        a,
        h,
        y,
        _,
        g,
        S,
        b
      );
    }
  }, Je = (f, a, h, y, _, g, S) => {
    const b = a.el = f.el;
    let { patchFlag: v, dynamicChildren: m, dirs: P } = a;
    v |= f.patchFlag & 16;
    const I = f.props || k, O = a.props || k;
    let M;
    if (h && at(h, !1), (M = O.onVnodeBeforeUpdate) && je(M, h, a, f), P && ft(a, f, h, "beforeUpdate"), h && at(h, !0), // #6385 the old vnode may be a user-wrapped non-isomorphic block
    // Force full diff when block metadata is unstable.
    m && (!f.dynamicChildren || f.dynamicChildren.length !== m.length) && (v = 0, S = !1, m = null), (I.innerHTML && O.innerHTML == null || I.textContent && O.textContent == null) && u(b, ""), m ? Se(
      f.dynamicChildren,
      m,
      b,
      h,
      y,
      jn(a, _),
      g
    ) : S || V(
      f,
      a,
      b,
      null,
      h,
      y,
      jn(a, _),
      g,
      !1
    ), v > 0) {
      if (v & 16)
        lt(b, I, O, h, _);
      else if (v & 2 && I.class !== O.class && i(b, "class", null, O.class, _), v & 4 && i(b, "style", I.style, O.style, _), v & 8) {
        const N = a.dynamicProps;
        for (let B = 0; B < N.length; B++) {
          const J = N[B], X = I[J], se = O[J];
          (se !== X || J === "value") && i(b, J, X, se, _, h);
        }
      }
      v & 1 && f.children !== a.children && u(b, a.children);
    } else !S && m == null && lt(b, I, O, h, _);
    ((M = O.onVnodeUpdated) || P) && pe(() => {
      M && je(M, h, a, f), P && ft(a, f, h, "updated");
    }, y);
  }, Se = (f, a, h, y, _, g, S) => {
    for (let b = 0; b < a.length; b++) {
      const v = f[b], m = a[b], P = (
        // oldVNode may be an errored async setup() component inside Suspense
        // which will not have a mounted element
        v.el && // - In the case of a Fragment, we need to provide the actual parent
        // of the Fragment itself so it can move its children.
        (v.type === Le || // - In the case of different nodes, there is going to be a replacement
        // which also requires the correct parent container
        !At(v, m) || // - In the case of a component, it could contain anything.
        v.shapeFlag & 198) ? p(v.el) : (
          // In other cases, the parent container is not actually used so we
          // just pass the block element here to avoid a DOM parentNode call.
          h
        )
      );
      C(
        v,
        m,
        P,
        null,
        y,
        _,
        g,
        S,
        !0
      );
    }
  }, lt = (f, a, h, y, _) => {
    if (a !== h) {
      if (a !== k)
        for (const g in a)
          !Rt(g) && !(g in h) && i(
            f,
            g,
            a[g],
            null,
            _,
            y
          );
      for (const g in h) {
        if (Rt(g)) continue;
        const S = h[g], b = a[g];
        S !== b && g !== "value" && i(f, g, b, S, _, y);
      }
      "value" in h && i(f, "value", a.value, h.value, _);
    }
  }, ct = (f, a, h, y, _, g, S, b, v) => {
    const m = a.el = f ? f.el : l(""), P = a.anchor = f ? f.anchor : l("");
    let { patchFlag: I, dynamicChildren: O, slotScopeIds: M } = a;
    M && (b = b ? b.concat(M) : M), f == null ? (s(m, h, y), s(P, h, y), he(
      // #10007
      // such fragment like `<></>` will be compiled into
      // a fragment which doesn't have a children.
      // In this case fallback to an empty array
      a.children || [],
      h,
      P,
      _,
      g,
      S,
      b,
      v
    )) : I > 0 && I & 64 && O && // #2715 the previous fragment could've been a BAILed one as a result
    // of renderSlot() with no valid children
    f.dynamicChildren && f.dynamicChildren.length === O.length ? (Se(
      f.dynamicChildren,
      O,
      h,
      _,
      g,
      S,
      b
    ), // #2080 if the stable fragment has a key, it's a <template v-for> that may
    //  get moved around. Make sure all root level vnodes inherit el.
    // #2134 or if it's a component root, it may also get moved around
    // as the component is being moved.
    (a.key != null || _ && a === _.subTree) && zr(
      f,
      a,
      !0
      /* shallow */
    )) : V(
      f,
      a,
      h,
      P,
      _,
      g,
      S,
      b,
      v
    );
  }, ee = (f, a, h, y, _, g, S, b, v) => {
    a.slotScopeIds = b, f == null ? a.shapeFlag & 512 ? _.ctx.activate(
      a,
      h,
      y,
      S,
      v
    ) : gt(
      a,
      h,
      y,
      _,
      g,
      S,
      v
    ) : qt(f, a, v);
  }, gt = (f, a, h, y, _, g, S) => {
    const b = f.component = Zo(
      f,
      y,
      _
    );
    if (hs(f) && (b.ctx.renderer = Ct), el(b, !1, S), b.asyncDep) {
      if (_ && _.registerDep(b, te, S), !f.el) {
        const v = b.subTree = Xe(tt);
        L(null, v, a, h), f.placeholder = v.el;
      }
    } else
      te(
        b,
        f,
        a,
        h,
        _,
        g,
        S
      );
  }, qt = (f, a, h) => {
    const y = a.component = f.component;
    if (Ro(f, a, h))
      if (y.asyncDep && !y.asyncResolved) {
        z(y, a, h);
        return;
      } else
        y.next = a, y.update();
    else
      a.el = f.el, y.vnode = a;
  }, te = (f, a, h, y, _, g, S) => {
    const b = () => {
      if (f.isMounted) {
        let { next: I, bu: O, u: M, parent: N, vnode: B } = f;
        {
          const Re = Yr(f);
          if (Re) {
            I && (I.el = B.el, z(f, I, S)), Re.asyncDep.then(() => {
              pe(() => {
                f.isUnmounted || m();
              }, _);
            });
            return;
          }
        }
        let J = I, X;
        at(f, !1), I ? (I.el = B.el, z(f, I, S)) : I = B, O && tn(O), (X = I.props && I.props.onVnodeBeforeUpdate) && je(X, N, I, B), at(f, !0);
        const se = Rs(f), $e = f.subTree;
        f.subTree = se, C(
          $e,
          se,
          // parent may have changed if it's in a teleport
          p($e.el),
          // anchor may have changed if it's in a fragment
          Me($e),
          f,
          _,
          g
        ), I.el = se.el, J === null && Fo(f, se.el), M && pe(M, _), (X = I.props && I.props.onVnodeUpdated) && pe(
          () => je(X, N, I, B),
          _
        );
      } else {
        let I;
        const { el: O, props: M } = a, { bm: N, m: B, parent: J, root: X, type: se } = f, $e = Dt(a);
        at(f, !1), N && tn(N), !$e && (I = M && M.onVnodeBeforeMount) && je(I, J, a), at(f, !0);
        {
          X.ce && X.ce._hasShadowRoot() && X.ce._injectChildStyle(
            se,
            f.parent ? f.parent.type : void 0
          );
          const Re = f.subTree = Rs(f);
          C(
            null,
            Re,
            h,
            y,
            f,
            _,
            g
          ), a.el = Re.el;
        }
        if (B && pe(B, _), !$e && (I = M && M.onVnodeMounted)) {
          const Re = a;
          pe(
            () => je(I, J, Re),
            _
          );
        }
        (a.shapeFlag & 256 || J && Dt(J.vnode) && J.vnode.shapeFlag & 256) && f.a && pe(f.a, _), f.isMounted = !0, a = h = y = null;
      }
    };
    f.scope.on();
    const v = f.effect = new lr(b);
    f.scope.off();
    const m = f.update = v.run.bind(v), P = f.job = v.runIfDirty.bind(v);
    P.i = f, P.id = f.uid, v.scheduler = () => us(P), at(f, !0), m();
  }, z = (f, a, h) => {
    a.component = f;
    const y = f.vnode.props;
    f.vnode = a, f.next = null, No(f, a.props, y, h), Uo(f, a.children, h), Ze(), Is(f), Qe();
  }, V = (f, a, h, y, _, g, S, b, v = !1) => {
    const m = f && f.children, P = f ? f.shapeFlag : 0, I = a.children, { patchFlag: O, shapeFlag: M } = a;
    if (O > 0) {
      if (O & 128) {
        Ee(
          m,
          I,
          h,
          y,
          _,
          g,
          S,
          b,
          v
        );
        return;
      } else if (O & 256) {
        We(
          m,
          I,
          h,
          y,
          _,
          g,
          S,
          b,
          v
        );
        return;
      }
    }
    M & 8 ? (P & 16 && me(m, _, g), I !== m && u(h, I)) : P & 16 ? M & 16 ? Ee(
      m,
      I,
      h,
      y,
      _,
      g,
      S,
      b,
      v
    ) : me(m, _, g, !0) : (P & 8 && u(h, ""), M & 16 && he(
      I,
      h,
      y,
      _,
      g,
      S,
      b,
      v
    ));
  }, We = (f, a, h, y, _, g, S, b, v) => {
    f = f || bt, a = a || bt;
    const m = f.length, P = a.length, I = Math.min(m, P);
    let O;
    for (O = 0; O < I; O++) {
      const M = a[O] = v ? qe(a[O]) : He(a[O]);
      C(
        f[O],
        M,
        h,
        null,
        _,
        g,
        S,
        b,
        v
      );
    }
    m > P ? me(
      f,
      _,
      g,
      !0,
      !1,
      I
    ) : he(
      a,
      h,
      y,
      _,
      g,
      S,
      b,
      v,
      I
    );
  }, Ee = (f, a, h, y, _, g, S, b, v) => {
    let m = 0;
    const P = a.length;
    let I = f.length - 1, O = P - 1;
    for (; m <= I && m <= O; ) {
      const M = f[m], N = a[m] = v ? qe(a[m]) : He(a[m]);
      if (At(M, N))
        C(
          M,
          N,
          h,
          null,
          _,
          g,
          S,
          b,
          v
        );
      else
        break;
      m++;
    }
    for (; m <= I && m <= O; ) {
      const M = f[I], N = a[O] = v ? qe(a[O]) : He(a[O]);
      if (At(M, N))
        C(
          M,
          N,
          h,
          null,
          _,
          g,
          S,
          b,
          v
        );
      else
        break;
      I--, O--;
    }
    if (m > I) {
      if (m <= O) {
        const M = O + 1, N = M < P ? a[M].el : y;
        for (; m <= O; )
          C(
            null,
            a[m] = v ? qe(a[m]) : He(a[m]),
            h,
            N,
            _,
            g,
            S,
            b,
            v
          ), m++;
      }
    } else if (m > O)
      for (; m <= I; )
        E(f[m], _, g, !0), m++;
    else {
      const M = m, N = m, B = /* @__PURE__ */ new Map();
      for (m = N; m <= O; m++) {
        const _e = a[m] = v ? qe(a[m]) : He(a[m]);
        _e.key != null && B.set(_e.key, m);
      }
      let J, X = 0;
      const se = O - N + 1;
      let $e = !1, Re = 0;
      const It = new Array(se);
      for (m = 0; m < se; m++) It[m] = 0;
      for (m = M; m <= I; m++) {
        const _e = f[m];
        if (X >= se) {
          E(_e, _, g, !0);
          continue;
        }
        let Fe;
        if (_e.key != null)
          Fe = B.get(_e.key);
        else
          for (J = N; J <= O; J++)
            if (It[J - N] === 0 && At(_e, a[J])) {
              Fe = J;
              break;
            }
        Fe === void 0 ? E(_e, _, g, !0) : (It[Fe - N] = m + 1, Fe >= Re ? Re = Fe : $e = !0, C(
          _e,
          a[Fe],
          h,
          null,
          _,
          g,
          S,
          b,
          v
        ), X++);
      }
      const ys = $e ? Wo(It) : bt;
      for (J = ys.length - 1, m = se - 1; m >= 0; m--) {
        const _e = N + m, Fe = a[_e], vs = a[_e + 1], bs = _e + 1 < P ? (
          // #13559, #14173 fallback to el placeholder for unresolved async component
          vs.el || Xr(vs)
        ) : y;
        It[m] === 0 ? C(
          null,
          Fe,
          h,
          bs,
          _,
          g,
          S,
          b,
          v
        ) : $e && (J < 0 || m !== ys[J] ? Be(Fe, h, bs, 2) : J--);
      }
    }
  }, Be = (f, a, h, y, _ = null) => {
    const { el: g, type: S, transition: b, children: v, shapeFlag: m } = f;
    if (m & 6) {
      Be(f.component.subTree, a, h, y);
      return;
    }
    if (m & 128) {
      f.suspense.move(a, h, y);
      return;
    }
    if (m & 64) {
      S.move(f, a, h, Ct);
      return;
    }
    if (S === Le) {
      s(g, a, h);
      for (let I = 0; I < v.length; I++)
        Be(v[I], a, h, y);
      s(f.anchor, a, h);
      return;
    }
    if (S === Nn) {
      H(f, a, h);
      return;
    }
    if (y !== 2 && m & 1 && b)
      if (y === 0)
        b.persisted && !g[Rn] ? s(g, a, h) : (b.beforeEnter(g), s(g, a, h), pe(() => b.enter(g), _));
      else {
        const { leave: I, delayLeave: O, afterLeave: M } = b, N = () => {
          f.ctx.isUnmounted ? r(g) : s(g, a, h);
        }, B = () => {
          const J = g._isLeaving || !!g[Rn];
          g._isLeaving && g[Rn](
            !0
            /* cancelled */
          ), b.persisted && !J ? N() : I(g, () => {
            N(), M && M();
          });
        };
        O ? O(g, N, B) : B();
      }
    else
      s(g, a, h);
  }, E = (f, a, h, y = !1, _ = !1) => {
    const {
      type: g,
      props: S,
      ref: b,
      children: v,
      dynamicChildren: m,
      shapeFlag: P,
      patchFlag: I,
      dirs: O,
      cacheIndex: M,
      memo: N
    } = f;
    if (I === -2 && (_ = !1), b != null && (Ze(), Nt(b, null, h, f, !0), Qe()), M != null && (a.renderCache[M] = void 0), P & 256) {
      a.ctx.deactivate(f);
      return;
    }
    const B = P & 1 && O, J = !Dt(f);
    let X;
    if (J && (X = S && S.onVnodeBeforeUnmount) && je(X, a, f), P & 6)
      Ce(f.component, h, y);
    else {
      if (P & 128) {
        f.suspense.unmount(h, y);
        return;
      }
      B && ft(f, null, a, "beforeUnmount"), P & 64 ? f.type.remove(
        f,
        a,
        h,
        Ct,
        y
      ) : m && // #5154
      // when v-once is used inside a block, setBlockTracking(-1) marks the
      // parent block with hasOnce: true
      // so that it doesn't take the fast path during unmount - otherwise
      // components nested in v-once are never unmounted.
      !m.hasOnce && // #1153: fast path should not be taken for non-stable (v-for) fragments
      (g !== Le || I > 0 && I & 64) ? me(
        m,
        a,
        h,
        !1,
        !0
      ) : (g === Le && I & 384 || !_ && P & 16) && me(v, a, h), y && j(f);
    }
    const se = N != null && M == null;
    (J && (X = S && S.onVnodeUnmounted) || B || se) && pe(() => {
      X && je(X, a, f), B && ft(f, null, a, "unmounted"), se && (f.el = null);
    }, h);
  }, j = (f) => {
    const { type: a, el: h, anchor: y, transition: _ } = f;
    if (a === Le) {
      ne(h, y);
      return;
    }
    if (a === Nn) {
      A(f);
      return;
    }
    const g = () => {
      r(h), _ && !_.persisted && _.afterLeave && _.afterLeave();
    };
    if (f.shapeFlag & 1 && _ && !_.persisted) {
      const { leave: S, delayLeave: b } = _, v = () => S(h, g);
      b ? b(f.el, g, v) : v();
    } else
      g();
  }, ne = (f, a) => {
    let h;
    for (; f !== a; )
      h = w(f), r(f), f = h;
    r(a);
  }, Ce = (f, a, h) => {
    const { bum: y, scope: _, job: g, subTree: S, um: b, m: v, a: m } = f;
    Ns(v), Ns(m), y && tn(y), _.stop(), g && (g.flags |= 8, E(S, f, a, h)), b && pe(b, a), pe(() => {
      f.isUnmounted = !0;
    }, a);
  }, me = (f, a, h, y = !1, _ = !1, g = 0) => {
    for (let S = g; S < f.length; S++)
      E(f[S], a, h, y, _);
  }, Me = (f) => {
    if (f.shapeFlag & 6)
      return Me(f.component.subTree);
    if (f.shapeFlag & 128)
      return f.suspense.next();
    const a = w(f.anchor || f.el), h = a && a[io];
    return h ? w(h) : a;
  };
  let mt = !1;
  const _s = (f, a, h) => {
    let y;
    f == null ? a._vnode && (E(a._vnode, null, null, !0), y = a._vnode.component) : C(
      a._vnode || null,
      f,
      a,
      null,
      null,
      null,
      h
    ), a._vnode = f, mt || (mt = !0, Is(y), Er(), mt = !1);
  }, Ct = {
    p: C,
    um: E,
    m: Be,
    r: j,
    mt: gt,
    mc: he,
    pc: V,
    pbc: Se,
    n: Me,
    o: e
  };
  return {
    render: _s,
    hydrate: void 0,
    createApp: To(_s)
  };
}
function jn({ type: e, props: t }, n) {
  return n === "svg" && e === "foreignObject" || n === "mathml" && e === "annotation-xml" && t && t.encoding && t.encoding.includes("html") ? void 0 : n;
}
function at({ effect: e, job: t }, n) {
  n ? (e.flags |= 32, t.flags |= 4) : (e.flags &= -33, t.flags &= -5);
}
function Jo(e, t) {
  return (!e || e && !e.pendingBranch) && t && !t.persisted;
}
function zr(e, t, n = !1) {
  const s = e.children, r = t.children;
  if ($(s) && $(r))
    for (let i = 0; i < s.length; i++) {
      const o = s[i];
      let l = r[i];
      l.shapeFlag & 1 && !l.dynamicChildren && ((l.patchFlag <= 0 || l.patchFlag === 32) && (l = r[i] = qe(r[i]), l.el = o.el), !n && l.patchFlag !== -2 && zr(o, l)), l.type === In && (l.patchFlag === -1 && (l = r[i] = qe(l)), l.el = o.el), l.type === tt && !l.el && (l.el = o.el);
    }
}
function Wo(e) {
  const t = e.slice(), n = [0];
  let s, r, i, o, l;
  const c = e.length;
  for (s = 0; s < c; s++) {
    const d = e[s];
    if (d !== 0) {
      if (r = n[n.length - 1], e[r] < d) {
        t[s] = r, n.push(s);
        continue;
      }
      for (i = 0, o = n.length - 1; i < o; )
        l = i + o >> 1, e[n[l]] < d ? i = l + 1 : o = l;
      d < e[n[i]] && (i > 0 && (t[s] = n[i - 1]), n[i] = s);
    }
  }
  for (i = n.length, o = n[i - 1]; i-- > 0; )
    n[i] = o, o = t[o];
  return n;
}
function Yr(e) {
  const t = e.subTree.component;
  if (t)
    return t.asyncDep && !t.asyncResolved ? t : Yr(t);
}
function Ns(e) {
  if (e)
    for (let t = 0; t < e.length; t++)
      e[t].flags |= 8;
}
function Xr(e) {
  if (e.placeholder)
    return e.placeholder;
  const t = e.component;
  return t ? Xr(t.subTree) : null;
}
const Zr = (e) => e.__isSuspense;
function Bo(e, t) {
  t && t.pendingBranch ? $(e) ? t.effects.push(...e) : t.effects.push(e) : Qi(e);
}
const Le = /* @__PURE__ */ Symbol.for("v-fgt"), In = /* @__PURE__ */ Symbol.for("v-txt"), tt = /* @__PURE__ */ Symbol.for("v-cmt"), Nn = /* @__PURE__ */ Symbol.for("v-stc"), pt = [];
let ye = null;
function Q(e = !1) {
  pt.push(ye = e ? null : []);
}
function Qr() {
  pt.pop(), ye = pt[pt.length - 1] || null;
}
let Vt = 1;
function Ds(e, t = !1) {
  Vt += e, e < 0 && ye && t && (ye.hasOnce = !0);
}
function ei(e) {
  return e.dynamicChildren = Vt > 0 ? ye || bt : null, Qr(), Vt > 0 && ye && ye.push(e), e;
}
function le(e, t, n, s, r, i) {
  return ei(
    Z(
      e,
      t,
      n,
      s,
      r,
      i,
      !0
    )
  );
}
function ti(e, t, n, s, r) {
  return ei(
    Xe(
      e,
      t,
      n,
      s,
      r,
      !0
    )
  );
}
function ni(e) {
  return e ? e.__v_isVNode === !0 : !1;
}
function At(e, t) {
  return e.type === t.type && e.key === t.key;
}
const si = ({ key: e }) => e ?? null, rn = ({
  ref: e,
  ref_key: t,
  ref_for: n
}) => (typeof e == "number" && (e = "" + e), e != null ? Y(e) || /* @__PURE__ */ fe(e) || F(e) ? { i: we, r: e, k: t, f: !!n } : e : null);
function Z(e, t = null, n = null, s = 0, r = null, i = e === Le ? 0 : 1, o = !1, l = !1) {
  const c = {
    __v_isVNode: !0,
    __v_skip: !0,
    type: e,
    props: t,
    key: t && si(t),
    ref: t && rn(t),
    scopeId: Ir,
    slotScopeIds: null,
    children: n,
    component: null,
    suspense: null,
    ssContent: null,
    ssFallback: null,
    dirs: null,
    transition: null,
    el: null,
    anchor: null,
    target: null,
    targetStart: null,
    targetAnchor: null,
    staticCount: 0,
    shapeFlag: i,
    patchFlag: s,
    dynamicProps: r,
    dynamicChildren: null,
    appContext: null,
    ctx: we
  };
  return l ? (dn(c, n), i & 128 && e.normalize(c)) : n && (c.shapeFlag |= Y(n) ? 8 : 16), Vt > 0 && // avoid a block node from tracking itself
  !o && // has current parent block
  ye && // presence of a patch flag indicates this node needs patching on updates.
  // component nodes also should always be patched, because even if the
  // component doesn't need to update, it needs to persist the instance on to
  // the next vnode so that it can be properly unmounted later.
  (c.patchFlag > 0 || i & 6) && // the EVENTS flag is only for hydration and if it is the only flag, the
  // vnode should not be considered dynamic due to handler caching.
  c.patchFlag !== 32 && ye.push(c), c;
}
const Xe = ko;
function ko(e, t = null, n = null, s = 0, r = null, i = !1) {
  if ((!e || e === yo) && (e = tt), ni(e)) {
    const l = Et(
      e,
      t,
      !0
      /* mergeRef: true */
    );
    return n && dn(l, n), Vt > 0 && !i && ye && (l.shapeFlag & 6 ? ye[ye.indexOf(e)] = l : ye.push(l)), l.patchFlag = -2, l;
  }
  if (rl(e) && (e = e.__vccOpts), t) {
    t = Go(t);
    let { class: l, style: c } = t;
    l && !Y(l) && (t.class = ns(l)), W(c) && (/* @__PURE__ */ as(c) && !$(c) && (c = oe({}, c)), t.style = ts(c));
  }
  const o = Y(e) ? 1 : Zr(e) ? 128 : Sn(e) ? 64 : W(e) ? 4 : F(e) ? 2 : 0;
  return Z(
    e,
    t,
    n,
    s,
    r,
    o,
    i,
    !0
  );
}
function Go(e) {
  return e ? /* @__PURE__ */ as(e) || Jr(e) ? oe({}, e) : e : null;
}
function Et(e, t, n = !1, s = !1) {
  const { props: r, ref: i, patchFlag: o, children: l, transition: c } = e, d = t ? zo(r || {}, t) : r, u = {
    __v_isVNode: !0,
    __v_skip: !0,
    type: e.type,
    props: d,
    key: d && si(d),
    ref: t && t.ref ? (
      // #2078 in the case of <component :is="vnode" ref="extra"/>
      // if the vnode itself already has a ref, cloneVNode will need to merge
      // the refs so the single vnode can be set on multiple refs
      n && i ? $(i) ? i.concat(rn(t)) : [i, rn(t)] : rn(t)
    ) : i,
    scopeId: e.scopeId,
    slotScopeIds: e.slotScopeIds,
    children: l,
    target: e.target,
    targetStart: e.targetStart,
    targetAnchor: e.targetAnchor,
    staticCount: e.staticCount,
    shapeFlag: e.shapeFlag,
    // if the vnode is cloned with extra props, we can no longer assume its
    // existing patch flag to be reliable and need to add the FULL_PROPS flag.
    // note: preserve flag for fragments since they use the flag for children
    // fast paths only.
    patchFlag: t && e.type !== Le ? o === -1 ? 16 : o | 16 : o,
    dynamicProps: e.dynamicProps,
    dynamicChildren: e.dynamicChildren,
    appContext: e.appContext,
    dirs: e.dirs,
    transition: c,
    // These should technically only be non-null on mounted VNodes. However,
    // they *should* be copied for kept-alive vnodes. So we just always copy
    // them since them being non-null during a mount doesn't affect the logic as
    // they will simply be overwritten.
    component: e.component,
    suspense: e.suspense,
    ssContent: e.ssContent && Et(e.ssContent),
    ssFallback: e.ssFallback && Et(e.ssFallback),
    placeholder: e.placeholder,
    el: e.el,
    anchor: e.anchor,
    ctx: e.ctx,
    ce: e.ce
  };
  return c && s && ds(
    u,
    c.clone(u)
  ), u;
}
function qo(e = " ", t = 0) {
  return Xe(In, null, e, t);
}
function be(e = "", t = !1) {
  return t ? (Q(), ti(tt, null, e)) : Xe(tt, null, e);
}
function He(e) {
  return e == null || typeof e == "boolean" ? Xe(tt) : $(e) ? Xe(
    Le,
    null,
    // #3666, avoid reference pollution when reusing vnode
    e.slice()
  ) : ni(e) ? qe(e) : Xe(In, null, String(e));
}
function qe(e) {
  return e.el === null && e.patchFlag !== -1 || e.memo ? e : Et(e);
}
function dn(e, t) {
  let n = 0;
  const { shapeFlag: s } = e;
  if (t == null)
    t = null;
  else if ($(t))
    n = 16;
  else if (typeof t == "object")
    if (s & 65) {
      const r = t.default;
      r && (r._c && (r._d = !1), dn(e, r()), r._c && (r._d = !0));
      return;
    } else {
      n = 32;
      const r = t._;
      !r && !Jr(t) ? t._ctx = we : r === 3 && we && (we.slots._ === 1 ? t._ = 1 : (t._ = 2, e.patchFlag |= 1024));
    }
  else if (F(t)) {
    if (s & 65) {
      dn(e, { default: t });
      return;
    }
    t = { default: t, _ctx: we }, n = 32;
  } else
    t = String(t), s & 64 ? (n = 16, t = [qo(t)]) : n = 8;
  e.children = t, e.shapeFlag |= n;
}
function zo(...e) {
  const t = {};
  for (let n = 0; n < e.length; n++) {
    const s = e[n];
    for (const r in s)
      if (r === "class")
        t.class !== s.class && (t.class = ns([t.class, s.class]));
      else if (r === "style")
        t.style = ts([t.style, s.style]);
      else if (mn(r)) {
        const i = t[r], o = s[r];
        o && i !== o && !($(i) && i.includes(o)) ? t[r] = i ? [].concat(i, o) : o : o == null && i == null && // mergeProps({ 'onUpdate:modelValue': undefined }) should not retain
        // the model listener.
        !_n(r) && (t[r] = o);
      } else r !== "" && (t[r] = s[r]);
  }
  return t;
}
function je(e, t, n, s = null) {
  Pe(e, t, 7, [
    n,
    s
  ]);
}
const Yo = Lr();
let Xo = 0;
function Zo(e, t, n) {
  const s = e.type, r = (t ? t.appContext : e.appContext) || Yo, i = {
    uid: Xo++,
    vnode: e,
    type: s,
    parent: t,
    appContext: r,
    root: null,
    // to be immediately set
    next: null,
    subTree: null,
    // will be set synchronously right after creation
    effect: null,
    update: null,
    // will be set synchronously right after creation
    job: null,
    scope: new wi(
      !0
      /* detached */
    ),
    render: null,
    proxy: null,
    exposed: null,
    exposeProxy: null,
    withProxy: null,
    provides: t ? t.provides : Object.create(r.provides),
    ids: t ? t.ids : ["", 0, 0],
    accessCache: null,
    renderCache: [],
    // local resolved assets
    components: null,
    directives: null,
    // resolved props and emits options
    propsOptions: Br(s, r),
    emitsOptions: Hr(s, r),
    // emit
    emit: null,
    // to be set immediately
    emitted: null,
    // props default value
    propsDefaults: k,
    // inheritAttrs
    inheritAttrs: s.inheritAttrs,
    // state
    ctx: k,
    data: k,
    props: k,
    attrs: k,
    slots: k,
    refs: k,
    setupState: k,
    setupContext: null,
    // suspense related
    suspense: n,
    suspenseId: n ? n.pendingId : 0,
    asyncDep: null,
    asyncResolved: !1,
    // lifecycle hooks
    // not using enums here because it results in computed properties
    isMounted: !1,
    isUnmounted: !1,
    isDeactivated: !1,
    bc: null,
    c: null,
    bm: null,
    m: null,
    bu: null,
    u: null,
    um: null,
    bum: null,
    da: null,
    a: null,
    rtg: null,
    rtc: null,
    ec: null,
    sp: null
  };
  return i.ctx = { _: i }, i.root = t ? t.root : i, i.emit = Oo.bind(null, i), e.ce && e.ce(i), i;
}
let de = null;
const Qo = () => de || we;
let hn, Jt;
{
  const e = vn(), t = (n, s) => {
    let r;
    return (r = e[n]) || (r = e[n] = []), r.push(s), (i) => {
      r.length > 1 ? r.forEach((o) => o(i)) : r[0](i);
    };
  };
  hn = t(
    "__VUE_INSTANCE_SETTERS__",
    (n) => de = n
  ), Jt = t(
    "__VUE_SSR_SETTERS__",
    (n) => Wt = n
  );
}
const Gt = (e) => {
  const t = de;
  return hn(e), e.scope.on(), () => {
    e.scope.off(), hn(t);
  };
}, Ls = () => {
  de && de.scope.off(), hn(null);
};
function ri(e) {
  return e.vnode.shapeFlag & 4;
}
let Wt = !1;
function el(e, t = !1, n = !1) {
  t && Jt(t);
  const { props: s, children: r } = e.vnode, i = ri(e);
  jo(e, s, i, t), Ho(e, r, n || t);
  const o = i ? tl(e, t) : void 0;
  return t && Jt(!1), o;
}
function tl(e, t) {
  const n = e.type;
  e.accessCache = /* @__PURE__ */ Object.create(null), e.proxy = new Proxy(e.ctx, bo);
  const { setup: s } = n;
  if (s) {
    Ze();
    const r = e.setupContext = s.length > 1 ? sl(e) : null, i = Gt(e), o = kt(
      s,
      e,
      0,
      [
        e.props,
        r
      ]
    ), l = Qs(o);
    if (Qe(), i(), (l || e.sp) && !Dt(e) && $r(e), l) {
      if (o.then(Ls, Ls), t)
        return o.then((c) => {
          Jt(!0);
          try {
            Hs(e, c, t);
          } finally {
            Jt(!1);
          }
        }).catch((c) => {
          xn(c, e, 0);
        });
      e.asyncDep = o;
    } else
      Hs(e, o);
  } else
    ii(e);
}
function Hs(e, t, n) {
  F(t) ? e.type.__ssrInlineRender ? e.ssrRender = t : e.render = t : W(t) && (e.setupState = wr(t)), ii(e);
}
function ii(e, t, n) {
  const s = e.type;
  e.render || (e.render = s.render || Ke);
  {
    const r = Gt(e);
    Ze();
    try {
      wo(e);
    } finally {
      Qe(), r();
    }
  }
}
const nl = {
  get(e, t) {
    return ce(e, "get", ""), e[t];
  }
};
function sl(e) {
  const t = (n) => {
    e.exposed = n || {};
  };
  return {
    attrs: new Proxy(e.attrs, nl),
    slots: e.slots,
    emit: e.emit,
    expose: t
  };
}
function Tn(e) {
  return e.exposed ? e.exposeProxy || (e.exposeProxy = new Proxy(wr(Ki(e.exposed)), {
    get(t, n) {
      if (n in t)
        return t[n];
      if (n in Lt)
        return Lt[n](e);
    },
    has(t, n) {
      return n in t || n in Lt;
    }
  })) : e.proxy;
}
function rl(e) {
  return F(e) && "__vccOpts" in e;
}
const rt = (e, t) => /* @__PURE__ */ Gi(e, t, Wt), il = "3.5.42";
let Yn;
const Us = typeof window < "u" && window.trustedTypes;
if (Us)
  try {
    Yn = /* @__PURE__ */ Us.createPolicy("vue", {
      createHTML: (e) => e
    });
  } catch {
  }
const oi = Yn ? (e) => Yn.createHTML(e) : (e) => e, ol = "http://www.w3.org/2000/svg", ll = "http://www.w3.org/1998/Math/MathML", Ge = typeof document < "u" ? document : null, Ks = Ge && /* @__PURE__ */ Ge.createElement("template"), cl = {
  insert: (e, t, n) => {
    t.insertBefore(e, n || null);
  },
  remove: (e) => {
    const t = e.parentNode;
    t && t.removeChild(e);
  },
  createElement: (e, t, n, s) => {
    const r = t === "svg" ? Ge.createElementNS(ol, e) : t === "mathml" ? Ge.createElementNS(ll, e) : n ? Ge.createElement(e, { is: n }) : Ge.createElement(e);
    return e === "select" && s && s.multiple != null && r.setAttribute("multiple", s.multiple), r;
  },
  createText: (e) => Ge.createTextNode(e),
  createComment: (e) => Ge.createComment(e),
  setText: (e, t) => {
    e.nodeValue = t;
  },
  setElementText: (e, t) => {
    e.textContent = t;
  },
  parentNode: (e) => e.parentNode,
  nextSibling: (e) => e.nextSibling,
  querySelector: (e) => Ge.querySelector(e),
  setScopeId(e, t) {
    e.setAttribute(t, "");
  },
  // __UNSAFE__
  // Reason: innerHTML.
  // Static content here can only come from compiled templates.
  // As long as the user only uses trusted templates, this is safe.
  insertStaticContent(e, t, n, s, r, i) {
    const o = n ? n.previousSibling : t.lastChild;
    if (r && (r === i || r.nextSibling))
      for (; t.insertBefore(r.cloneNode(!0), n), !(r === i || !(r = r.nextSibling)); )
        ;
    else {
      Ks.innerHTML = oi(
        s === "svg" ? `<svg>${e}</svg>` : s === "mathml" ? `<math>${e}</math>` : e
      );
      const l = Ks.content;
      if (s === "svg" || s === "mathml") {
        const c = l.firstChild;
        for (; c.firstChild; )
          l.appendChild(c.firstChild);
        l.removeChild(c);
      }
      t.insertBefore(l, n);
    }
    return [
      // first
      o ? o.nextSibling : t.firstChild,
      // last
      n ? n.previousSibling : t.lastChild
    ];
  }
}, fl = /* @__PURE__ */ Symbol("_vtc");
function al(e, t, n) {
  const s = e[fl];
  s && (t = (t ? [t, ...s] : [...s]).join(" ")), t == null ? e.removeAttribute("class") : n ? e.setAttribute("class", t) : e.className = t;
}
const pn = /* @__PURE__ */ Symbol("_vod"), li = /* @__PURE__ */ Symbol("_vsh"), ul = {
  // used for prop mismatch check during hydration
  name: "show",
  beforeMount(e, { value: t }, { transition: n }) {
    e[pn] = e.style.display === "none" ? "" : e.style.display, n && t ? n.beforeEnter(e) : Ot(e, t);
  },
  mounted(e, { value: t }, { transition: n }) {
    n && t && n.enter(e);
  },
  updated(e, { value: t, oldValue: n }, { transition: s }) {
    !t != !n && (s ? t ? (s.beforeEnter(e), Ot(e, !0), s.enter(e)) : s.leave(e, () => {
      Ot(e, !1);
    }) : Ot(e, t));
  },
  beforeUnmount(e, { value: t }) {
    Ot(e, t);
  }
};
function Ot(e, t) {
  e.style.display = t ? e[pn] : "none", e[li] = !t;
}
const dl = /* @__PURE__ */ Symbol(""), hl = /(?:^|;)\s*display\s*:/;
function pl(e, t, n) {
  const s = e.style, r = Y(n);
  let i = !1;
  if (n && !r) {
    if (t)
      if (Y(t))
        for (const o of t.split(";")) {
          const l = o.slice(0, o.indexOf(":")).trim();
          n[l] == null && Mt(s, l, "");
        }
      else
        for (const o in t)
          n[o] == null && Mt(s, o, "");
    for (const o in n) {
      o === "display" && (i = !0);
      const l = n[o];
      l != null ? ml(
        e,
        o,
        !Y(t) && t ? t[o] : void 0,
        l
      ) || Mt(s, o, l) : Mt(s, o, "");
    }
  } else if (r) {
    if (t !== n) {
      const o = s[dl];
      o && (n += ";" + o), s.cssText = n, i = hl.test(n);
    }
  } else t && e.removeAttribute("style");
  pn in e && (e[pn] = i ? s.display : "", e[li] && (s.display = "none"));
}
const Zt = /\s*!important$/;
function Mt(e, t, n) {
  if ($(n))
    n.forEach((s) => Mt(e, t, s));
  else if (n == null && (n = ""), t.startsWith("--"))
    Zt.test(n) ? e.setProperty(t, n.replace(Zt, ""), "important") : e.setProperty(t, n);
  else {
    const s = gl(e, t);
    Zt.test(n) ? e.setProperty(
      ot(s),
      n.replace(Zt, ""),
      "important"
    ) : e[s] = n;
  }
}
const Vs = ["Webkit", "Moz", "ms"], Dn = {};
function gl(e, t) {
  const n = Dn[t];
  if (n)
    return n;
  let s = Te(t);
  if (s !== "filter" && s in e)
    return Dn[t] = s;
  s = nr(s);
  for (let r = 0; r < Vs.length; r++) {
    const i = Vs[r] + s;
    if (i in e)
      return Dn[t] = i;
  }
  return t;
}
function ml(e, t, n, s) {
  return e.tagName === "TEXTAREA" && (t === "width" || t === "height") && Y(s) && n === s;
}
const Js = "http://www.w3.org/1999/xlink";
function Ws(e, t, n, s, r, i = vi(t)) {
  s && t.startsWith("xlink:") ? n == null ? e.removeAttributeNS(Js, t.slice(6, t.length)) : e.setAttributeNS(Js, t, n) : n == null || i && !rr(n) ? e.removeAttribute(t) : e.setAttribute(
    t,
    i ? "" : Ve(n) ? String(n) : n
  );
}
function Bs(e, t, n, s, r) {
  if (t === "innerHTML" || t === "textContent") {
    n != null && (e[t] = t === "innerHTML" ? oi(n) : n);
    return;
  }
  const i = e.tagName;
  if (t === "value" && i !== "PROGRESS" && // custom elements may use _value internally
  !i.includes("-")) {
    const l = i === "OPTION" ? e.getAttribute("value") || "" : e.value, c = n == null ? (
      // #11647: value should be set as empty string for null and undefined,
      // but <input type="checkbox"> should be set as 'on'.
      e.type === "checkbox" ? "on" : ""
    ) : String(n);
    (l !== c || !("_value" in e)) && (e.value = c), n == null && e.removeAttribute(t), e._value = n;
    return;
  }
  let o = !1;
  if (n === "" || n == null) {
    const l = typeof e[t];
    l === "boolean" ? n = rr(n) : n == null && l === "string" ? (n = "", o = !0) : l === "number" && (n = 0, o = !0);
  }
  try {
    e[t] = n;
  } catch {
  }
  o && e.removeAttribute(r || t);
}
function vt(e, t, n, s) {
  e.addEventListener(t, n, s);
}
function _l(e, t, n, s) {
  e.removeEventListener(t, n, s);
}
const ks = /* @__PURE__ */ Symbol("_vei");
function yl(e, t, n, s, r = null) {
  const i = e[ks] || (e[ks] = {}), o = i[t];
  if (s && o)
    o.value = s;
  else {
    const [l, c] = wl(t);
    if (s) {
      const d = i[t] = El(
        s,
        r
      );
      vt(e, l, d, c);
    } else o && (_l(e, l, o, c), i[t] = void 0);
  }
}
const vl = /(Once|Passive|Capture)$/, bl = /^on:?(?:Once|Passive|Capture)$/;
function wl(e) {
  let t, n;
  for (; (n = e.match(vl)) && !bl.test(e); )
    t || (t = {}), e = e.slice(0, e.length - n[1].length), t[n[1].toLowerCase()] = !0;
  return [e[2] === ":" ? e.slice(3) : ot(e.slice(2)), t];
}
let Ln = 0;
const xl = /* @__PURE__ */ Promise.resolve(), Sl = () => Ln || (xl.then(() => Ln = 0), Ln = Date.now());
function El(e, t) {
  const n = (s) => {
    if (!s._vts)
      s._vts = Date.now();
    else if (s._vts <= n.attached)
      return;
    const r = n.value;
    if ($(r)) {
      const i = s.stopImmediatePropagation;
      s.stopImmediatePropagation = () => {
        i.call(s), s._stopped = !0;
      };
      const o = r.slice(), l = [s];
      for (let c = 0; c < o.length && !s._stopped; c++) {
        const d = o[c];
        d && Pe(
          d,
          t,
          5,
          l
        );
      }
    } else
      Pe(
        r,
        t,
        5,
        [s]
      );
  };
  return n.value = e, n.attached = Sl(), n;
}
const Gs = (e) => e.charCodeAt(0) === 111 && e.charCodeAt(1) === 110 && // lowercase letter
e.charCodeAt(2) > 96 && e.charCodeAt(2) < 123, Cl = (e, t, n, s, r, i) => {
  const o = r === "svg";
  t === "class" ? al(e, s, o) : t === "style" ? pl(e, n, s) : mn(t) ? _n(t) || yl(e, t, n, s, i) : (t[0] === "." ? (t = t.slice(1), !0) : t[0] === "^" ? (t = t.slice(1), !1) : Il(e, t, s, o)) ? (Bs(e, t, s), !e.tagName.includes("-") && (t === "value" || t === "checked" || t === "selected") && Ws(e, t, s, o, i, t !== "value")) : /* #11081 force set props for possible async custom element */ e._isVueCE && // #12408 check if it's declared prop or it's async custom element
  (Tl(e, t) || // @ts-expect-error _def is private
  e._def.__asyncLoader && (/[A-Z]/.test(t) || !Y(s))) ? Bs(e, Te(t), s, i, t) : (t === "true-value" ? e._trueValue = s : t === "false-value" && (e._falseValue = s), Ws(e, t, s, o));
};
function Il(e, t, n, s) {
  if (s)
    return !!(t === "innerHTML" || t === "textContent" || t in e && Gs(t) && F(n));
  if (t === "spellcheck" || t === "draggable" || t === "translate" || t === "autocorrect" || t === "sandbox" && e.tagName === "IFRAME" || t === "form" || t === "list" && e.tagName === "INPUT" || t === "type" && e.tagName === "TEXTAREA")
    return !1;
  if (t === "width" || t === "height") {
    const r = e.tagName;
    if (r === "IMG" || r === "VIDEO" || r === "CANVAS" || r === "SOURCE")
      return !1;
  }
  return Gs(t) && Y(n) ? !1 : t in e;
}
function Tl(e, t) {
  const n = (
    // @ts-expect-error _def is private
    e._def.props
  );
  if (!n)
    return !1;
  const s = Te(t);
  return Array.isArray(n) ? n.some((r) => Te(r) === s) : Object.keys(n).some((r) => Te(r) === s);
}
const qs = (e) => {
  const t = e.props["onUpdate:modelValue"] || !1;
  return $(t) ? (n) => tn(t, n) : t;
};
function Al(e) {
  e.target.composing = !0;
}
function zs(e) {
  const t = e.target;
  t.composing && (t.composing = !1, t.dispatchEvent(new Event("input")));
}
const Qt = /* @__PURE__ */ Symbol("_assign"), en = /* @__PURE__ */ Symbol("_initialValue");
function Hn(e, t, n) {
  return t && (e = e.trim()), n && (e = es(e)), e;
}
const Ol = {
  created(e, { modifiers: { lazy: t, trim: n, number: s } }, r) {
    e.parentNode && (e.type === "text" ? e[en] = e.defaultValue.replace(/[\r\n]/g, "") : e.type === "textarea" && (e[en] = e.defaultValue.replace(/\r\n?/g, `
`))), e[Qt] = qs(r);
    const i = s || r.props && r.props.type === "number";
    vt(e, t ? "change" : "input", (o) => {
      o.target.composing || e[Qt](Hn(e.value, n, i));
    }), (n || i) && vt(e, "change", () => {
      e.value = Hn(e.value, n, i);
    }), t || (vt(e, "compositionstart", Al), vt(e, "compositionend", zs), vt(e, "change", zs));
  },
  // set value on mounted so it's after min/max for type="range"
  mounted(e, { value: t, modifiers: { trim: n, number: s } }) {
    const r = t ?? "", i = e[en];
    delete e[en], i !== void 0 && (e.type === "text" || e.type === "textarea") && e.value !== i ? e[Qt](Hn(e.value, n, s)) : e.value = r;
  },
  beforeUpdate(e, { value: t, oldValue: n, modifiers: { lazy: s, trim: r, number: i } }, o) {
    if (e[Qt] = qs(o), e.composing) return;
    const l = (i || e.type === "number") && !/^0\d/.test(e.value) ? es(e.value) : e.value, c = t ?? "";
    if (l === c)
      return;
    const d = e.getRootNode();
    (d instanceof Document || d instanceof ShadowRoot) && d.activeElement === e && e.type !== "range" && (s && t === n || r && e.value.trim() === c) || (e.value = c);
  }
}, Pl = ["ctrl", "shift", "alt", "meta"], Ml = {
  stop: (e) => e.stopPropagation(),
  prevent: (e) => e.preventDefault(),
  self: (e) => e.target !== e.currentTarget,
  ctrl: (e) => !e.ctrlKey,
  shift: (e) => !e.shiftKey,
  alt: (e) => !e.altKey,
  meta: (e) => !e.metaKey,
  left: (e) => "button" in e && e.button !== 0,
  middle: (e) => "button" in e && e.button !== 1,
  right: (e) => "button" in e && e.button !== 2,
  exact: (e, t) => Pl.some((n) => e[`${n}Key`] && !t.includes(n))
}, Ys = (e, t) => {
  if (!e) return e;
  const n = e._withMods || (e._withMods = {}), s = t.join(".");
  return n[s] || (n[s] = ((r, ...i) => {
    for (let o = 0; o < t.length; o++) {
      const l = Ml[t[o]];
      if (l && l(r, t)) return;
    }
    return e(r, ...i);
  }));
}, $l = {
  esc: "escape",
  space: " ",
  up: "arrow-up",
  left: "arrow-left",
  right: "arrow-right",
  down: "arrow-down",
  delete: "backspace"
}, Rl = (e, t) => {
  const n = e._withKeys || (e._withKeys = {}), s = t.join(".");
  return n[s] || (n[s] = ((r) => {
    if (!("key" in r))
      return;
    const i = ot(r.key);
    if (t.some(
      (o) => o === i || $l[o] === i
    ))
      return e(r);
  }));
}, Fl = /* @__PURE__ */ oe({ patchProp: Cl }, cl);
let Xs;
function jl() {
  return Xs || (Xs = Ko(Fl));
}
const Nl = ((...e) => {
  const t = jl().createApp(...e), { mount: n } = t;
  return t.mount = (s) => {
    const r = Ll(s);
    if (!r) return;
    const i = t._component;
    !F(i) && !i.render && !i.template && (i.template = r.innerHTML), r.nodeType === 1 && (r.textContent = "");
    const o = n(r, !1, Dl(r));
    return r instanceof Element && (r.removeAttribute("v-cloak"), r.setAttribute("data-v-app", "")), o;
  }, t;
});
function Dl(e) {
  if (e instanceof SVGElement)
    return "svg";
  if (typeof MathMLElement == "function" && e instanceof MathMLElement)
    return "mathml";
}
function Ll(e) {
  return Y(e) ? document.querySelector(e) : e;
}
const Hl = ["aria-label"], Ul = ["disabled"], Kl = {
  key: 0,
  role: "alert"
}, Vl = ["aria-busy"], Jl = { key: 0 }, Wl = ["onClick"], Bl = { key: 1 }, kl = /* @__PURE__ */ Mr({
  __name: "FilePicker",
  props: {
    host: {},
    accept: {},
    title: {}
  },
  emits: ["pick", "close"],
  setup(e, { emit: t }) {
    const n = e, s = t, r = /* @__PURE__ */ ie(""), i = /* @__PURE__ */ ie(""), o = /* @__PURE__ */ ie([]), l = /* @__PURE__ */ ie(!1), c = /* @__PURE__ */ ie(""), d = rt(() => o.value.filter((x) => x.type === "directory" || n.accept.some((T) => x.name.toLowerCase().endsWith(T))));
    let u = 0;
    async function p(x) {
      const T = ++u;
      l.value = !0, c.value = "";
      try {
        const C = await n.host.fs.ls(x);
        if (T !== u) return;
        r.value = x, i.value = x, o.value = C;
      } catch (C) {
        T === u && (c.value = String(C));
      } finally {
        T === u && (l.value = !1);
      }
    }
    function w(x) {
      const T = [r.value.replace(/\/$/, ""), x.name].filter(Boolean).join("/");
      x.type === "directory" ? p(T) : s("pick", T);
    }
    return ps(() => p("")), (x, T) => (Q(), le("div", {
      class: "shade",
      onClick: T[5] || (T[5] = Ys((C) => s("close"), ["self"]))
    }, [
      Z("section", {
        role: "dialog",
        "aria-modal": "true",
        "aria-label": e.title,
        onKeydown: T[4] || (T[4] = Rl((C) => s("close"), ["esc"]))
      }, [
        Z("header", null, [
          Z(
            "strong",
            null,
            Ie(e.title),
            1
            /* TEXT */
          ),
          Z("button", {
            "aria-label": "Close file picker",
            onClick: T[0] || (T[0] = (C) => s("close"))
          }, "×")
        ]),
        Z(
          "form",
          {
            onSubmit: T[3] || (T[3] = Ys((C) => p(i.value), ["prevent"]))
          },
          [
            Z("button", {
              type: "button",
              disabled: !r.value,
              "aria-label": "Parent folder",
              onClick: T[1] || (T[1] = (C) => p(r.value.split("/").slice(0, -1).join("/")))
            }, "↑", 8, Ul),
            Tr(Z(
              "input",
              {
                "onUpdate:modelValue": T[2] || (T[2] = (C) => i.value = C),
                "aria-label": "Folder path",
                placeholder: "Workspace"
              },
              null,
              512
              /* NEED_PATCH */
            ), [
              [Ol, i.value]
            ]),
            T[6] || (T[6] = Z(
              "button",
              null,
              "Open folder",
              -1
              /* CACHED */
            ))
          ],
          32
          /* NEED_HYDRATION */
        ),
        c.value ? (Q(), le(
          "p",
          Kl,
          Ie(c.value),
          1
          /* TEXT */
        )) : be("v-if", !0),
        Z("div", {
          class: "files",
          "aria-busy": l.value
        }, [
          l.value ? (Q(), le("p", Jl, "Loading…")) : be("v-if", !0),
          (Q(!0), le(
            Le,
            null,
            vo(d.value, (C) => (Q(), le("button", {
              key: C.name,
              onClick: (G) => w(C)
            }, Ie(C.type === "directory" ? "▸" : "·") + " " + Ie(C.name), 9, Wl))),
            128
            /* KEYED_FRAGMENT */
          )),
          !l.value && !d.value.length ? (Q(), le("p", Bl, "No supported images in this folder.")) : be("v-if", !0)
        ], 8, Vl)
      ], 40, Hl)
    ]));
  }
}), ci = (e, t) => {
  const n = e.__vccOpts || e;
  for (const [s, r] of t)
    n[s] = r;
  return n;
}, Gl = /* @__PURE__ */ ci(kl, [["__scopeId", "data-v-a1a4179b"]]), ql = "https://lib.imjoy.io/imjoy-loader.js", zl = "https://ij.imjoy.io";
function Yl(e, t) {
  const n = `atriumResult${t.replaceAll("_", "")}`, s = [...e.matchAll(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\/\/[^\n]*|\/\*[\s\S]*?\*\/|[A-Za-z_$][\w$]*|[^\s]/g)].filter((c) => !c[0].startsWith("//") && !c[0].startsWith("/*")), r = [], i = [];
  let o = 0, l = !1;
  for (let c = 0; c < s.length; c++) {
    const d = s[c];
    if (d[0] === "function" && (l = !0), d[0] === "{")
      i.push(l), l && o++, l = !1;
    else if (d[0] === "}")
      i.pop() && o--;
    else if (d[0] === "return" && !o) {
      const u = d.index + d[0].length;
      let p = c + 1, w = 0;
      for (; p < s.length; p++) {
        const C = s[p][0];
        if (w === 0 && (C === ";" || C === "}")) break;
        (C === "(" || C === "[") && w++, (C === ")" || C === "]") && w--;
      }
      const x = s[p]?.index ?? e.length, T = e.slice(u, x).trim();
      r.push({
        start: d.index,
        end: x + (s[p]?.[0] === ";" ? 1 : 0),
        value: T ? `{ ${n} = ${T}; return ${JSON.stringify(t)} + ${n}; }` : `{ return ${JSON.stringify(t)}; }`
      }), c = p - 1;
    }
  }
  for (const c of r.reverse()) e = e.slice(0, c.start) + c.value + e.slice(c.end);
  return `var ${n};
` + e + `
return ${JSON.stringify(t)};`;
}
async function gn(e, t, n = "") {
  const s = `__ATRIUM_RESULT_${crypto.randomUUID().replaceAll("-", "")}__`, r = Yl(t, s), i = (o) => {
    const l = typeof o == "string" ? o : o instanceof Error ? o.message : void 0;
    return l?.startsWith(s) ? l.slice(s.length) : void 0;
  };
  try {
    const o = await e.runMacro(r, n), l = i(o);
    if (l !== void 0) return l;
    throw new Error("ImageJ did not confirm the macro result");
  } catch (o) {
    const l = i(o);
    if (l !== void 0) return l;
    throw o;
  }
}
async function $t(e) {
  const t = await gn(
    e,
    'if (nImages == 0) return "0"; getDimensions(w, h, c, z, t); return "" + nImages + "\\n" + getImageID() + "\\n" + w + "\\n" + h + "\\n" + c + "\\n" + z + "\\n" + t + "\\n" + getTitle();'
  ), [n, s, ...r] = t.split(`
`), i = Number(n);
  if (!Number.isInteger(i) || i < 0) throw new Error(`ImageJ returned invalid image state: ${JSON.stringify(t.slice(0, 160))}`);
  if (!i) return { image_count: i, current_image: null };
  const o = Number(s), l = r.slice(0, 5).map(Number);
  if (!Number.isInteger(o) || l.length !== 5 || !l.every((c) => Number.isInteger(c) && c > 0))
    throw new Error("ImageJ could not read the current image");
  return { image_count: i, current_image: { id: o, title: r.slice(5).join(`
`), dimensions: l } };
}
async function Xl(e) {
  if (!(await $t(e)).current_image) throw new Error("ImageJ has no open image to capture");
  const n = await e.getImage("png"), s = n instanceof ArrayBuffer ? new Uint8Array(n) : ArrayBuffer.isView(n) ? new Uint8Array(n.buffer, n.byteOffset, n.byteLength) : Array.isArray(n) ? Uint8Array.from(n) : null;
  if (!s?.length) throw new Error("ImageJ did not return PNG bytes");
  if (s[0] !== 137 || s[1] !== 80 || s[2] !== 78 || s[3] !== 71)
    throw new Error("ImageJ returned an invalid PNG");
  let r = "";
  for (let i = 0; i < s.length; i += 8192)
    r += String.fromCharCode(...s.subarray(i, i + 8192));
  return `data:image/png;base64,${btoa(r)}`;
}
let Un = null;
function Zl(e) {
  return new Promise((t, n) => {
    const s = document.querySelector(`script[src="${e}"]`);
    if (s) {
      s.addEventListener("load", () => t()), s.addEventListener("error", () => n(new Error(`could not load ${e}`))), window.loadImJoyCore && t();
      return;
    }
    const r = document.createElement("script");
    r.src = e, r.async = !0, r.onload = () => t(), r.onerror = () => n(new Error(`could not load ${e}`)), document.head.appendChild(r);
  });
}
async function Ql() {
  if (Un || (Un = Zl(ql)), await Un, !window.loadImJoyCore) throw new Error("the ImJoy loader did not register");
}
async function ec(e) {
  await Ql();
  const t = await window.loadImJoyCore(), n = new t.ImJoy({ imjoy_api: {} });
  await n.start({ workspace: "default" }), n.event_bus.on("add_window", (r) => {
    if (document.getElementById(r.window_id)) return;
    const i = document.createElement("div");
    i.id = r.window_id, i.style.width = "100%", i.style.height = "100%", e.appendChild(i);
  });
  const s = await n.api.createWindow({
    src: zl,
    name: "ImageJ",
    type: "window",
    // Fill the container rather than ImJoy's default window box.
    fullscreen: !1,
    w: 40,
    h: 30
  });
  return {
    api: s,
    dispose: () => {
      try {
        s.close?.();
      } catch {
      }
      try {
        n.destroy?.();
      } catch {
      }
      e.replaceChildren();
    }
  };
}
async function tc(e, t, n, s) {
  s?.("fetching");
  const r = await fetch(t);
  if (!r.ok) throw new Error(`could not fetch the file (${r.status})`);
  const i = await r.arrayBuffer();
  s?.("decoding", i.byteLength);
  const o = await fi(e);
  await e.viewImage(i, { name: n }), await rc(e, o);
  const l = await e.getDimensions();
  if (!Array.isArray(l) || !l[0])
    throw new Error(`ImageJ could not read ${n}`);
  return l;
}
async function fi(e) {
  const t = Number(await gn(e, 'return "" + nImages;'));
  if (!Number.isInteger(t) || t < 0) throw new Error("ImageJ returned an invalid image count");
  return t;
}
const nc = 12e4, sc = 300;
async function rc(e, t) {
  const n = Date.now() + nc;
  for (; Date.now() < n; ) {
    if (await fi(e) > t) return;
    await new Promise((s) => setTimeout(s, sc));
  }
  throw new Error("ImageJ did not open the file — it may be a format it cannot read");
}
const ic = { class: "imagej" }, oc = { class: "bar" }, lc = ["disabled", "title"], cc = { class: "current" }, fc = {
  key: 0,
  class: "dims"
}, ac = {
  key: 1,
  class: "busy"
}, uc = ["title"], dc = {
  key: 3,
  class: "hint"
}, hc = {
  key: 4,
  class: "degraded",
  title: "The ImJoy core could not be fetched, so ImageJ runs without the RPC channel: no confirmation that a file opened, and a second file reloads the app."
}, pc = ["title"], gc = ["src", "title"], mc = {
  key: 1,
  class: "overlay"
}, _c = {
  key: 2,
  class: "overlay failed-overlay"
}, yc = /* @__PURE__ */ Mr({
  __name: "ImageJApp",
  props: {
    host: {},
    path: {},
    started: { type: Function },
    failed: { type: Function }
  },
  setup(e) {
    const t = (E) => E.split("/").pop() || E, n = [
      ".tif",
      ".tiff",
      ".ome.tif",
      ".ome.tiff",
      ".png",
      ".jpg",
      ".jpeg",
      ".gif",
      ".bmp",
      ".dcm",
      ".dicom",
      ".fits",
      ".pgm",
      ".avi",
      ".nrrd",
      ".zarr",
      ".ome.zarr"
    ], s = e, r = /* @__PURE__ */ ie(null), i = /* @__PURE__ */ ie("starting"), o = /* @__PURE__ */ ie(!1), l = /* @__PURE__ */ ie(""), c = /* @__PURE__ */ ie(""), d = /* @__PURE__ */ ie(!1), u = /* @__PURE__ */ ie(void 0), p = { ready: !0 }, w = /* @__PURE__ */ ie(null), x = /* @__PURE__ */ ie(null), T = /* @__PURE__ */ ie(null), C = /* @__PURE__ */ ie(0), G = /* @__PURE__ */ ie(0);
    let L = null, R = null, H = null, A = "", D = { image_count: 0, current_image: null };
    const ve = s.started, ge = s.failed;
    function he() {
      if (o.value) throw new Error("ImageJ is in limited mode: its RPC control channel is unavailable");
      if (!R) throw new Error("ImageJ is still starting; its RPC control channel is not ready");
      return R;
    }
    async function Je(E, j) {
      if (A) throw new Error(`ImageJ is busy with ${A}; wait for it before issuing another operation`);
      A = E;
      try {
        return await j();
      } finally {
        A = "";
      }
    }
    async function Se() {
      const E = {
        app: "imagej",
        path: ee.value ?? null,
        status: i.value,
        connected: !!R && !o.value,
        degraded: o.value,
        error: c.value
      };
      return !R || o.value || A ? { ...E, ...D, busy: A || null, state_fresh: !1 } : (await Je("reading image state", async () => {
        D = await $t(he());
      }), w.value = D.current_image?.dimensions ?? null, { ...E, ...D, busy: null, state_fresh: !0 });
    }
    async function lt(E) {
      if (he(), A) throw new Error(`ImageJ is busy with ${A}`);
      if (typeof E != "string" || !E.trim()) throw new Error("open needs a sandbox path");
      return u.value = E, await Ee(!0), s.host.setState({ path: E }), await Se();
    }
    const ct = {
      actions: {
        open: ({ path: E }) => lt(E),
        status: Se,
        run_macro: async ({ macro: E, args: j, expected_image_id: ne }) => {
          const Ce = he();
          if (typeof E != "string" || !E.trim()) throw new Error("run_macro needs a macro string");
          if (E.length > 1e5) throw new Error("macro is too large (maximum 100000 characters)");
          if (j !== void 0 && typeof j != "string") throw new Error("macro args must be a string");
          if (ne !== void 0 && !Number.isInteger(ne))
            throw new Error("expected_image_id must be the integer returned by desktop_read");
          const me = ne === void 0 ? "" : `if (nImages == 0 || getImageID() != ${ne}) return "__ATRIUM_IMAGE_CHANGED__";
`;
          return await Je("running macro", async () => {
            const Me = await gn(Ce, me + E, j);
            if (Me === "__ATRIUM_IMAGE_CHANGED__") throw new Error("the current ImageJ image changed; read this window again before running the macro");
            return D = await $t(Ce), w.value = D.current_image?.dimensions ?? null, { result: Me ?? null, ...D };
          });
        },
        select_image: async ({ title: E }) => {
          const j = he();
          if (typeof E != "string" || !E) throw new Error("select_image needs an image title");
          return await Je("selecting image", async () => {
            if (!(await gn(j, 'titles = getList("image.titles"); text = ""; for (i=0; i<titles.length; i++) { if (i>0) text=text+"\\n"; text=text+titles[i]; } return text;')).split(`
`).includes(E)) throw new Error(`no open ImageJ image named '${E}'`);
            if (await j.selectWindow(E), D = await $t(j), D.current_image?.title !== E)
              throw new Error("the requested ImageJ image is no longer active; read this window again");
            return w.value = D.current_image?.dimensions ?? null, D;
          });
        }
      },
      snapshot: () => Je("capturing image", () => Xl(he()))
    };
    for (const [E, j] of Object.entries(ct.actions))
      s.host.defineAction(E, async (ne) => {
        const Ce = await j(ne);
        return s.host.setState(await Se()), Ce;
      });
    s.host.onSnapshot(ct.snapshot);
    const ee = rt(() => u.value ?? s.path), gt = rt(() => ee.value ? t(ee.value) : "no file"), qt = rt(() => {
      if (!x.value) return "";
      const E = C.value ? ` · ${(C.value / 1e6).toFixed(1)} MB` : "", j = G.value > 2 ? ` · ${G.value}s` : "";
      return `${x.value}${E}${j}`;
    }), te = rt(() => {
      const E = T.value;
      if (!E) return "";
      const j = E.reason ? `ImageJ1 cannot read ${E.reason}` : "ImageJ1 cannot read this variant", ne = E.downsampled && E.downsampled > 1 ? `, and it was decimated 1/${E.downsampled} to fit a 32-bit JVM` : "";
      return `${j}, so the pod rewrote it as a plain uncompressed TIFF${ne}.`;
    }), z = rt(() => x.value === "decoding" && C.value > 2e7), V = rt(() => {
      const E = w.value;
      if (!E) return "";
      const [j, ne, Ce, me, Me] = E, mt = [Ce > 1 && `${Ce}c`, me > 1 && `${me}z`, Me > 1 && `${Me}t`].filter(Boolean);
      return `${j}×${ne}${mt.length ? ` · ${mt.join(" ")}` : ""}`;
    });
    async function We() {
      if (!(!r.value || R)) {
        i.value = "starting", c.value = "";
        try {
          const E = await ec(r.value);
          R = E.api, H = E.dispose, i.value = "ready";
        } catch (E) {
          console.warn("[atrium] ImJoy core unavailable, falling back to ?open=", E), o.value = !0, i.value = "ready", c.value = "", await Ee(), ge(new Error("ImageJ is in limited mode: its RPC control channel could not start"));
          return;
        }
        try {
          ee.value && await Ee(!0), ve();
        } catch (E) {
          ge(E instanceof Error ? E : new Error(String(E)));
        }
      }
    }
    async function Ee(E = !1) {
      if (!(!o.value && !R)) {
        if (A) {
          if (E) throw new Error(`ImageJ is busy with ${A}`);
          c.value = `ImageJ is busy with ${A}`;
          return;
        }
        A = "opening image", i.value = "opening", c.value = "", w.value = null, G.value = 0, C.value = 0, L = setInterval(() => {
          G.value += 1;
        }, 1e3);
        try {
          if (o.value)
            l.value = ee.value ? `https://ij.imjoy.io/?open=${encodeURIComponent(String((await s.host.call("prepare", { path: ee.value }, { timeoutMs: 6e5 })).url))}` : "https://ij.imjoy.io";
          else if (ee.value) {
            x.value = "preparing", T.value = null;
            const j = await s.host.call("prepare", { path: ee.value }, { timeoutMs: 6e5 });
            j.converted && (T.value = { reason: j.reason, downsampled: j.downsampled });
            const ne = j.url;
            w.value = await tc(
              R,
              ne,
              t(j.path === ee.value ? ee.value : j.path),
              (Ce, me) => {
                x.value = Ce, me && (C.value = me);
              }
            ), D = await $t(R);
          }
        } catch (j) {
          if (c.value = j instanceof Error ? j.message : String(j), E) throw j;
        } finally {
          L && clearInterval(L), L = null, x.value = null, i.value = "ready", A = "";
        }
      }
    }
    function Be(E) {
      d.value = !1, u.value = E, Ee().then(() => s.host.setState({ path: E }));
    }
    return ps(We), Fr(() => {
      L && clearInterval(L), H?.(), R = null;
    }), sn(() => p.ready, (E) => {
      E && ee.value && Ee();
    }), s.host.onState(async (E) => {
      typeof E.path == "string" && E.path !== ee.value && (u.value = E.path, await Ee(!0));
    }), (E, j) => (Q(), le("div", ic, [
      Z("header", oc, [
        Z("button", {
          class: "open",
          disabled: i.value === "starting",
          title: "Open a file from the sandbox",
          onClick: j[0] || (j[0] = (ne) => d.value = !0)
        }, [...j[2] || (j[2] = [
          Z(
            "span",
            { "aria-hidden": "true" },
            "＋",
            -1
            /* CACHED */
          ),
          Z(
            "span",
            null,
            "Open from sandbox",
            -1
            /* CACHED */
          )
        ])], 8, lc),
        Z(
          "span",
          cc,
          Ie(gt.value),
          1
          /* TEXT */
        ),
        V.value ? (Q(), le(
          "span",
          fc,
          Ie(V.value),
          1
          /* TEXT */
        )) : be("v-if", !0),
        i.value === "opening" ? (Q(), le(
          "span",
          ac,
          Ie(qt.value || "opening…"),
          1
          /* TEXT */
        )) : be("v-if", !0),
        T.value ? (Q(), le("span", {
          key: 2,
          class: "hint",
          title: te.value
        }, " rewritten for ImageJ" + Ie(T.value.downsampled && T.value.downsampled > 1 ? ` · 1/${T.value.downsampled} scale` : ""), 9, uc)) : be("v-if", !0),
        z.value ? (Q(), le("span", dc, " large images are slow here — Viv handles pyramids better ")) : be("v-if", !0),
        o.value ? (Q(), le("span", hc, "limited mode")) : be("v-if", !0),
        c.value ? (Q(), le("span", {
          key: 5,
          class: "failed",
          title: c.value
        }, Ie(c.value), 9, pc)) : be("v-if", !0)
      ]),
      Tr(Z(
        "div",
        {
          ref_key: "container",
          ref: r,
          class: "stage"
        },
        null,
        512
        /* NEED_PATCH */
      ), [
        [ul, !o.value]
      ]),
      o.value && l.value ? (Q(), le("iframe", {
        key: 0,
        class: "stage",
        src: l.value,
        title: gt.value
      }, null, 8, gc)) : be("v-if", !0),
      i.value === "starting" ? (Q(), le("div", mc, [...j[3] || (j[3] = [
        Z(
          "p",
          null,
          "Starting ImageJ.js…",
          -1
          /* CACHED */
        ),
        Z(
          "p",
          { class: "note" },
          " ImageJ is a Java application compiled to the browser; the first load takes a moment. ",
          -1
          /* CACHED */
        )
      ])])) : i.value === "error" ? (Q(), le("div", _c, [
        Z(
          "p",
          null,
          Ie(c.value),
          1
          /* TEXT */
        )
      ])) : be("v-if", !0),
      d.value ? (Q(), ti(Gl, {
        key: 3,
        accept: n,
        host: e.host,
        title: "Open in ImageJ.js",
        onPick: Be,
        onClose: j[1] || (j[1] = (ne) => d.value = !1)
      }, null, 8, ["host"])) : be("v-if", !0)
    ]));
  }
}), vc = /* @__PURE__ */ ci(yc, [["__scopeId", "data-v-b2d7c380"]]);
async function wc(e, t) {
  let n, s;
  const r = new Promise((o, l) => {
    n = o, s = l;
  }), i = Nl(vc, { host: e, started: n, failed: s });
  i.mount(t), window.addEventListener("pagehide", () => i.unmount(), { once: !0 }), await r;
}
export {
  wc as setup
};
