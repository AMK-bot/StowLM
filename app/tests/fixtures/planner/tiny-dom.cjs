/* Deliberately small SVG DOM for exercising BayPlan without a browser. */
'use strict';
const escapeXML = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
class Element {
  constructor(tag, document) {
    this.tagName = tag; this.nodeName = tag; this.nodeType = 1; this.ownerDocument = document;
    this.attributes = {}; this.children = []; this.parentNode = null; this.style = {};
    this.listeners = {}; this._text = ''; this.clientWidth = 760; this.clientHeight = 520;
    this.classList = {
      add: (...names) => this.setAttribute('class', [...new Set([...this.classes(), ...names])].join(' ')),
      remove: (...names) => this.setAttribute('class', this.classes().filter(n => !names.includes(n)).join(' ')),
      contains: name => this.classes().includes(name),
      toggle: (name, force) => { const yes = force === undefined ? !this.classes().includes(name) : force; this.classList[yes ? 'add' : 'remove'](name); return yes; },
    };
  }
  classes() { return (this.attributes.class || '').split(/\s+/).filter(Boolean); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  setAttributeNS(ns, name, value) { this.setAttribute(name, value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  hasAttribute(name) { return name in this.attributes; }
  removeAttribute(name) { delete this.attributes[name]; }
  appendChild(child) { if (child.parentNode) child.remove(); this.children.push(child); child.parentNode = this; return child; }
  append(...children) { children.forEach(child => this.appendChild(typeof child === 'string' ? this.ownerDocument.createTextNode(child) : child)); }
  insertBefore(child, next) { if (!next) return this.appendChild(child); if (child.parentNode) child.remove(); this.children.splice(this.children.indexOf(next), 0, child); child.parentNode = this; return child; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); child.parentNode = null; return child; }
  replaceChildren(...children) { this.children.forEach(child => { child.parentNode = null; }); this.children = []; this._text = ''; this.append(...children); }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children.at(-1) || null; }
  get parentElement() { return this.parentNode; }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set innerHTML(value) { if (value) throw Error('The tiny DOM does not parse HTML'); this.replaceChildren(); }
  get innerHTML() { return escapeXML(this._text) + this.children.map(c => c.outerHTML).join(''); }
  get outerHTML() { const attrs = {...this.attributes}; const style = Object.entries(this.style).map(([k,v]) => `${k}:${v}`).join(';'); if (style) attrs.style = style; return `<${this.tagName}${Object.entries(attrs).map(([k,v])=>` ${k}="${escapeXML(v)}"`).join('')}>${this.innerHTML}</${this.tagName}>`; }
  matches(selector) {
    const tag = selector.match(/^[\w-]+/); if (tag && tag[0].toLowerCase() !== this.tagName.toLowerCase()) return false;
    for (const match of selector.matchAll(/\.([\w-]+)/g)) if (!this.classes().includes(match[1])) return false;
    for (const match of selector.matchAll(/\[([^=\]]+)(?:=["']?([^"'\]]*)["']?)?\]/g)) if (!this.hasAttribute(match[1]) || (match[2] !== undefined && this.getAttribute(match[1]) !== match[2])) return false;
    return true;
  }
  querySelectorAll(selector) { const selectors = selector.split(',').map(s=>s.trim()); const result=[]; const visit = n => n.children.forEach(c => { if (selectors.some(s => c.matches(s))) result.push(c); visit(c); }); visit(this); return result; }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { for (let n=this; n; n=n.parentNode) if (n.matches(selector)) return n; return null; }
  contains(other) { for (let n=other; n; n=n.parentNode) if (n===this) return true; return false; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  removeEventListener(name, fn) { this.listeners[name] = (this.listeners[name] || []).filter(f=>f!==fn); }
  dispatchEvent(event) { event.target ||= this; event.currentTarget = this; event.preventDefault ||= ()=>{}; event.stopPropagation ||= ()=>{event.stopped=true;}; for (const fn of this.listeners[event.type] || []) fn(event); if (!event.stopped && event.bubbles !== false && this.parentNode) this.parentNode.dispatchEvent(event); return true; }
  getBoundingClientRect() { return this.rect || {x:0,y:0,left:0,top:0,width:760,height:520,right:760,bottom:520}; }
  setPointerCapture() {} releasePointerCapture() {} focus() {}
  get dataset() { return Object.fromEntries(Object.entries(this.attributes).filter(([k])=>k.startsWith('data-')).map(([k,v])=>[k.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase()),v])); }
}
class Document extends Element {
  constructor() { super('#document', null); this.ownerDocument = this; this.body = new Element('body', this); this.appendChild(this.body); }
  createElementNS(ns, tag) { return new Element(tag, this); }
  createElement(tag) { return new Element(tag, this); }
  createTextNode(value) { const n = new Element('#text', this); n.textContent=value; Object.defineProperty(n,'outerHTML',{get:()=>escapeXML(n.textContent)}); return n; }
}
function install() {
  const document = new Document(); global.document=document; global.window=global;
  global.Element=Element; global.SVGElement=Element; global.XMLSerializer=class { serializeToString(node) { return node.outerHTML; } };
  let time=0; global.performance={now:()=>time};
  global.requestAnimationFrame = fn => setImmediate(()=>{time+=16;fn(time);}); global.cancelAnimationFrame=clearImmediate;
  global.addEventListener = document.addEventListener.bind(document); global.removeEventListener=document.removeEventListener.bind(document);
  global.matchMedia = ()=>({matches:false}); global.ResizeObserver=class{observe(){} disconnect(){}};
  return {document, Element, host:document.body.appendChild(document.createElement('div'))};
}
module.exports={install,Element};
