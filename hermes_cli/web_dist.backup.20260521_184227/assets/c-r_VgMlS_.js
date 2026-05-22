import{d as je,w as ie,o as Fe,b as Re,e as c,f as m,j as S,t as T,u as _,n as L,F as W,p as $e,z as Oe,g as Be,r as w,c as k,h as u,i as We}from"./c-CAXDqyLB.js";import{i as x,h as le,t as Ne}from"./c-DUi1jDaF.js";import{E as Ve,i as Ge}from"./e-C4_WHn0v.js";import{r as Ke,d as se,p as ce,a as Xe,x as ue,b as de,m as Je}from"./c-NEJyELAf.js";import{m as Qe,a as Ye,c as Ze}from"./c-DCBDY9q2.js";const et=["src","alt"],tt={class:"file-preview-header__title"},nt={key:1,class:"file-preview-header__win-controls"},rt=["src"],at=["aria-label"],ot=["src"],it=["src"],lt=["src"],st={key:0,class:"file-preview-empty"},ct={key:1,class:"file-preview-empty file-preview-empty--error"},ut={key:0,class:"file-preview-loading"},dt={key:1,class:"file-preview-empty file-preview-empty--error"},ft=["src","alt"],mt={key:0,class:"file-preview-loading"},pt=["src"],vt=["innerHTML"],wt={key:0,class:"file-preview-toc"},gt={class:"file-preview-toc__list"},ht=["title","onClick"],yt=["srcdoc"],bt={key:6,class:"file-preview-content"},N=`
<script>
(function() {
  var style = document.createElement('style');
  style.textContent = 'a[data-anchor]{cursor:pointer;}';
  (document.head || document.documentElement).appendChild(style);

  document.addEventListener('click', function(e) {
    var anchor = e.target.closest ? e.target.closest('a') : null;
    if (!anchor) return;

    // data-anchor 属性（由宿主替换 href="#" 生成）→ scrollIntoView 页内滚动
    var dataAnchor = anchor.getAttribute('data-anchor');
    if (dataAnchor && dataAnchor.startsWith('#')) {
      e.preventDefault();
      e.stopPropagation();
      var targetId = decodeURIComponent(dataAnchor.slice(1));
      var target = document.getElementById(targetId)
        || document.querySelector('[name="' + CSS.escape(targetId) + '"]');
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
      return;
    }

    var href = anchor.getAttribute('href');
    if (!href) return;

    // 锚点链接：阻止默认导航，改用 scrollIntoView 页内滚动
    // （srcdoc iframe 中默认的锚点导航会导致白屏）
    if (href.startsWith('#')) {
      e.preventDefault();
      e.stopPropagation();
      var targetId = decodeURIComponent(href.slice(1));
      var target = document.getElementById(targetId)
        || document.querySelector('[name="' + CSS.escape(targetId) + '"]');
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
      return;
    }

    // http(s) 外链：阻止默认行为，通知外层用系统浏览器打开
    if (/^https?:\\/\\//i.test(href)) {
      e.preventDefault();
      e.stopPropagation();
      parent.postMessage({ type: 'openExternal', url: href }, '*');
    }
  }, true);

  // 拦截 JS 代码通过 location.hash 触发的锚点导航（srcdoc 中 hash 变更会导致白屏）
  // 覆盖 location.hash setter，改用 scrollIntoView
  try {
    var origDesc = Object.getOwnPropertyDescriptor(Location.prototype, 'hash')
      || Object.getOwnPropertyDescriptor(window.location, 'hash');
    if (origDesc && origDesc.set) {
      Object.defineProperty(window.location, 'hash', {
        set: function(val) {
          var id = (val || '').replace(/^#/, '');
          if (id) {
            var el = document.getElementById(id)
              || document.querySelector('[name="' + CSS.escape(id) + '"]');
            if (el) {
              el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
          }
        },
        get: origDesc.get ? origDesc.get.bind(window.location) : function() { return ''; },
        configurable: true,
      });
    }
  } catch(ex) {}

  // ESC 键：通知外层关闭预览窗口
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' || e.keyCode === 27) {
      e.preventDefault();
      e.stopPropagation();
      parent.postMessage({ type: 'closeWindow' }, '*');
    }
  }, true);
})();
<\/script>`,_t=je({name:"FilePreviewWindow",__name:"index",setup(xt){const M=navigator.platform.toLowerCase().includes("mac"),l=w(null),g=w(!1),fe=k(()=>{var e,t;return((t=(e=l.value)==null?void 0:e.localPath)==null?void 0:t.startsWith("ima-downloading://"))??!1}),p=w(""),D=w(!1),F=w(!1),A=w(!1),P=w(!1),I=w(!1),V=k(()=>{var e;return!!((e=l.value)!=null&&e.previewUrl)}),R=k(()=>{var e;return!!((e=l.value)!=null&&e.imageUrl)}),$=k(()=>{var t,n;const e=((n=(t=l.value)==null?void 0:t.type)==null?void 0:n.toLowerCase())??"";return e==="md"||e==="markdown"}),q=k(()=>{var t,n;const e=((n=(t=l.value)==null?void 0:t.type)==null?void 0:n.toLowerCase())??"";return e==="html"||e==="htm"}),G=k(()=>{var e;return $.value?Ne(((e=l.value)==null?void 0:e.content)??"",{allowHtml:!0,htmlImageMode:"figure",charLimit:2e6,parseLimit:2e6}):""}),me=k(()=>{var r;if(!q.value)return"";let e=(((r=l.value)==null?void 0:r.content)??"").replace(/^\uFEFF/,"").replace(/\r\n?/g,`
`);e=e.replace(/(<a\b[^>]*?)href\s*=\s*"(#[^"]*)"([^>]*>)/gi,'$1data-anchor="$2"$3'),e=e.replace(/(<a\b[^>]*?)href\s*=\s*'(#[^']*)'([^>]*>)/gi,'$1data-anchor="$2"$3');const t=e.indexOf("</head>");if(t>=0)return e.slice(0,t)+N+e.slice(t);const n=e.indexOf("<body");if(n>=0){const a=e.indexOf(">",n);if(a>=0)return e.slice(0,a+1)+N+e.slice(a+1)}return e+N}),K=w(null);let h=null;const C=w(null),E=w([]),U=w("");function pe(e,t){const n=e.trim().toLowerCase().replace(/[\s\u3000]+/g,"-").replace(/[^\p{L}\p{N}\-_]/gu,"")||"heading";let r=n,a=1;for(;t.has(r);)r=`${n}-${a++}`;return t.add(r),r}function ve(){var a;const e=C.value;if(!e){E.value=[];return}const t=e.querySelectorAll("h1, h2, h3"),n=new Set,r=[];t.forEach(i=>{var d;const s=((d=i.textContent)==null?void 0:d.trim())??"";s&&(i.id?n.add(i.id):i.id=pe(s,n),r.push({id:i.id,level:Number(i.tagName.substring(1)),text:s}))}),E.value=r,U.value=((a=r[0])==null?void 0:a.id)??""}function we(e){var r,a;const t=(r=C.value)==null?void 0:r.parentElement,n=(a=C.value)==null?void 0:a.querySelector(`#${CSS.escape(e)}`);!t||!n||(n.scrollIntoView({behavior:"smooth",block:"start"}),U.value=e)}let y=null;function ge(){var n;y==null||y.disconnect();const e=C.value;if(!e)return;const t=e.querySelectorAll("h1, h2, h3");t.length!==0&&(y=new IntersectionObserver(r=>{var i;const a=r.filter(s=>s.isIntersecting).sort((s,d)=>s.boundingClientRect.top-d.boundingClientRect.top);if(a.length>0){const s=a[0];(i=s==null?void 0:s.target)!=null&&i.id&&(U.value=s.target.id)}},{root:((n=C.value)==null?void 0:n.parentElement)??null,rootMargin:"0px 0px -66% 0px",threshold:0}),t.forEach(r=>y.observe(r)))}ie(()=>G.value,async()=>{if(!$.value){E.value=[];return}await We(),ve(),ge()});const he={md:Je,doc:de,docx:de,xls:ue,xlsx:ue,pdf:Xe,ppt:ce,pptx:ce,txt:se,html:le,htm:le,png:x,jpg:x,jpeg:x,webp:x,gif:x,bmp:x,svg:x,ico:x};function ye(e){return he[e.toLowerCase()]||se}const be=new Set(["png","jpg","jpeg","webp","gif","bmp","svg","ico"]),_e={png:"image/png",jpg:"image/jpeg",jpeg:"image/jpeg",webp:"image/webp",gif:"image/gif",bmp:"image/bmp",svg:"image/svg+xml",ico:"image/x-icon"};let b=null;function xe(e){return(e.type||e.name.split(".").pop()||"").toLowerCase()}function ke(){A.value=!1,P.value=!0}function Ie(e){A.value=!1;const t=e.target;if(!t)return;const n=t.naturalWidth||0,r=t.naturalHeight||0;if(n<=0||r<=0){I.value=!1;return}const a=r/n;I.value=a>1.6}function O(){var t,n;const e=(n=(t=window.electronAPI)==null?void 0:t.shell)==null?void 0:n.window;if(e!=null&&e.close){e.close();return}window.close()}function Ce(){var e,t,n,r;(r=(n=(t=(e=window.electronAPI)==null?void 0:e.shell)==null?void 0:t.window)==null?void 0:n.minimize)==null||r.call(n)}function Ee(){var e,t,n,r;(r=(n=(t=(e=window.electronAPI)==null?void 0:e.shell)==null?void 0:t.window)==null?void 0:n.maximize)==null||r.call(n)}let H,z;function X(e){var r,a;const t=e.data;if(!t||typeof t.type!="string")return;if(t.type==="closeWindow"){O();return}if(t.type!=="openExternal"||typeof t.url!="string")return;const n=t.url;/^https?:\/\//i.test(n)&&((a=(r=window.electronAPI)==null?void 0:r.integration)==null||a.openExternal(n).catch(()=>{}))}function J(){var t;const e=K.value;if(!e)return null;try{return e.contentDocument??((t=e.contentWindow)==null?void 0:t.document)??null}catch{return null}}function Se(e){return(e.documentElement.getAttribute("data-theme")||e.documentElement.getAttribute("data-appearance")||"")==="dark"?"dark":"light"}function Le(e){const t=e.dataset.qclawMermaidSource||e.getAttribute("data-src")||"";if(t.trim())return t.trim();const n=e.textContent??"";return n.trim()&&(e.dataset.qclawMermaidSource=n.trim()),n.trim()}function Me(e){var a;const t=e.split(`
`);if(((a=t[0])==null?void 0:a.trim())!=="gantt")return{source:e};let n="";return{source:t.filter((i,s)=>{var v;if(s===0)return!0;const d=i.match(/^\s*title\s+(.+)\s*$/);return d?(n=((v=d[1])==null?void 0:v.trim())??"",!1):!0}).join(`
`).trim(),title:n||void 0}}function Ae(e,t){if(!t)return;const n=e.ownerDocument.createElement("div");n.textContent=t,n.style.cssText="font-weight:600;font-size:16px;text-align:center;margin:0 0 8px;color:currentColor;",e.prepend(n)}function Pe(e){e.querySelectorAll('.mermaid[data-qclaw-mermaid-status="rendered"]').forEach(t=>{const n=t.dataset.qclawMermaidSource||"";n.trim()&&(t.textContent=n,t.dataset.qclawMermaidStatus="pending",t.removeAttribute("data-processed"))})}async function Q(){const e=J();if(!e||!q.value)return;const t=Se(e),n=Array.from(e.querySelectorAll(".mermaid"));for(const r of n){const a=r.dataset.qclawMermaidStatus;if(a==="rendering"||a==="rendered"||a==="error"||r.getAttribute("data-processed")==="true"||r.querySelector("svg"))continue;const i=Le(r);if(!i)continue;const s=Me(i);r.dataset.qclawMermaidStatus="rendering";try{const d=await Ve(s.source,t,!0);if(!r.isConnected)continue;r.innerHTML=d,Ae(r,s.title),r.dataset.qclawMermaidStatus="rendered",r.setAttribute("data-processed","true")}catch{if(r.isConnected){const v=r.ownerDocument.createElement("pre");v.style.cssText="white-space:pre-wrap;word-break:break-all;font-size:13px;padding:12px;margin:0;background:var(--code-bg,#f6f8fa);border-radius:6px;overflow-x:auto;",v.textContent=i,r.textContent="",r.appendChild(v),r.dataset.qclawMermaidStatus="error"}}}}function Te(e){h==null||h.disconnect(),h=new MutationObserver(()=>{Pe(e),Q()}),h.observe(e.documentElement,{attributes:!0,attributeFilter:["data-theme","data-appearance"]})}function De(){const e=J();e&&(Te(e),window.setTimeout(()=>{Q()},1200))}function Y(e){e.key==="Escape"&&(e.isComposing||(e.preventDefault(),O()))}function qe(e){const t=document.createElement("textarea");t.value=e,t.style.position="fixed",t.style.left="-9999px",t.style.top="-9999px",t.style.opacity="0",document.body.appendChild(t),t.select();try{return document.execCommand("copy")}catch{return!1}finally{document.body.removeChild(t)}}async function Ue(e){if(!e)return!1;try{return await navigator.clipboard.writeText(e),!0}catch{return qe(e)}}async function He(e){var v;const t=e.target,n=t==null?void 0:t.closest(".code-block-copy");if(!n)return;e.preventDefault(),e.stopPropagation();const r=n.closest(".code-block-wrapper"),a=((v=r==null?void 0:r.querySelector("pre code"))==null?void 0:v.textContent)??"",i=n.querySelector("span"),s=(i==null?void 0:i.textContent)||"复制",d=await Ue(a);i&&(i.textContent=d?"已复制":"复制失败",window.setTimeout(()=>{i.textContent=s},1500))}return Fe(()=>{var e,t,n,r,a,i,s,d,v,j,Z;H=(n=(t=(e=window.electronAPI)==null?void 0:e.shell)==null?void 0:t.filePreview)==null?void 0:n.onSetData(async o=>{var te,ne,re,ae,oe;if(o.imageUrl){A.value=!0,P.value=!1,I.value=!1,p.value="",g.value=!1,l.value=o;return}if(o.previewUrl){F.value=!0,p.value="",g.value=!1,l.value=o;return}if((te=o.localPath)!=null&&te.startsWith("ima-downloading://")&&!o.content&&!o.previewUrl){p.value="",g.value=!0,l.value={...o,content:""};return}if(o.content&&o.content.length>0){p.value="",g.value=!1,l.value=o;return}if(!o.localPath){p.value="缺少文件路径",l.value={...o,content:""};return}const ee=xe(o);if(be.has(ee)){g.value=!0,p.value="",P.value=!1,I.value=!1,l.value={...o,content:""};try{const f=await((re=(ne=window.electronAPI)==null?void 0:ne.fileSystem)==null?void 0:re.readFileAsArrayBuffer(o.localPath));if(!f){p.value="无法读取图片内容";return}b&&(URL.revokeObjectURL(b),b=null);const B=_e[ee]||"application/octet-stream",ze=new Blob([f],{type:B});b=URL.createObjectURL(ze),A.value=!0,l.value={...o,content:"",imageUrl:b}}catch(f){p.value=`读取失败: ${String((f==null?void 0:f.message)??f)}`}finally{g.value=!1}return}g.value=!0,p.value="",l.value={...o,content:""};try{const f=await((oe=(ae=window.electronAPI)==null?void 0:ae.fileSystem)==null?void 0:oe.readFileAsArrayBuffer(o.localPath));if(!f){p.value="无法读取文件内容";return}const B=new TextDecoder("utf-8").decode(f);l.value={...o,content:B}}catch(f){p.value=`读取失败: ${String((f==null?void 0:f.message)??f)}`}finally{g.value=!1}}),ie(()=>{var o;return(o=l.value)==null?void 0:o.name},o=>{document.title=o||"文件预览"},{immediate:!0}),M||((s=(i=(a=(r=window.electronAPI)==null?void 0:r.shell)==null?void 0:a.window)==null?void 0:i.isMaximized)==null||s.call(i).then(o=>{D.value=!!o}),z=(Z=(j=(v=(d=window.electronAPI)==null?void 0:d.shell)==null?void 0:v.window)==null?void 0:j.onMaximizeChange)==null?void 0:Z.call(j,o=>{D.value=o})),window.addEventListener("message",X),window.addEventListener("keydown",Y)}),Re(()=>{H==null||H(),z==null||z(),window.removeEventListener("message",X),window.removeEventListener("keydown",Y),y==null||y.disconnect(),h==null||h.disconnect(),b&&(URL.revokeObjectURL(b),b=null)}),(e,t)=>{var n;return u(),c("div",{class:L(["file-preview-window",{"file-preview-window--mac":_(M)}])},[m("div",{class:L(["file-preview-header",{"file-preview-header--mac":_(M),"file-preview-header--win":!_(M)}])},[l.value?(u(),c("img",{key:0,class:"file-preview-header__icon",src:ye(l.value.type),alt:l.value.type},null,8,et)):S("",!0),m("span",tt,T(((n=l.value)==null?void 0:n.name)||"文件预览"),1),_(M)?S("",!0):(u(),c("div",nt,[m("button",{class:"file-preview-header__btn","aria-label":"最小化",tabindex:"0",onClick:Ce},[m("img",{src:_(Qe),alt:"minimize",class:"file-preview-header__btn-icon"},null,8,rt)]),m("button",{class:"file-preview-header__btn","aria-label":D.value?"向下还原":"最大化",tabindex:"0",onClick:Ee},[D.value?(u(),c("img",{key:0,src:_(Ke),alt:"restore",class:"file-preview-header__btn-icon"},null,8,ot)):(u(),c("img",{key:1,src:_(Ye),alt:"maximize",class:"file-preview-header__btn-icon"},null,8,it))],8,at),m("button",{class:"file-preview-header__btn file-preview-header__btn--close","aria-label":"关闭",tabindex:"0",onClick:O},[m("img",{src:_(Ze),alt:"close",class:"file-preview-header__btn-icon"},null,8,lt)])]))],2),m("div",{class:L(["file-preview-body",{"file-preview-body--html":q.value&&!g.value&&!p.value,"file-preview-body--iframe":V.value,"file-preview-body--image":R.value,"file-preview-body--long-image":R.value&&I.value}])},[!l.value||g.value?(u(),c("div",st,T(g.value?fe.value?"正在加载预览...":"加载中...":"正在加载预览..."),1)):p.value?(u(),c("div",ct,T(p.value),1)):R.value?(u(),c(W,{key:2},[A.value?(u(),c("div",ut,[...t[1]||(t[1]=[m("span",null,"正在加载预览...",-1)])])):S("",!0),P.value?(u(),c("div",dt," 图片加载失败 ")):S("",!0),$e(m("img",{src:l.value.imageUrl,class:L(["file-preview-image",{"file-preview-image--long":I.value}]),alt:l.value.name,draggable:"false",onLoad:Ie,onError:ke},null,42,ft),[[Oe,!P.value]])],64)):V.value?(u(),c(W,{key:3},[F.value?(u(),c("div",mt,[...t[2]||(t[2]=[m("span",null,"正在加载预览...",-1)])])):S("",!0),m("iframe",{src:l.value.previewUrl,class:"file-preview-iframe",frameborder:"0",allow:"clipboard-read; clipboard-write",onLoad:t[0]||(t[0]=r=>F.value=!1)},null,40,pt)],64)):$.value?(u(),c("div",{key:4,class:L(["file-preview-markdown-wrap",{"file-preview-markdown-wrap--has-toc":E.value.length>=2}])},[m("div",{ref_key:"markdownBodyRef",ref:C,class:"file-preview-markdown",onClick:He,innerHTML:G.value},null,8,vt),E.value.length>=2?(u(),c("aside",wt,[t[3]||(t[3]=m("div",{class:"file-preview-toc__title"},"目录",-1)),m("ul",gt,[(u(!0),c(W,null,Be(E.value,r=>(u(),c("li",{key:r.id,class:L(["file-preview-toc__item",`file-preview-toc__item--lv${r.level}`,{"file-preview-toc__item--active":r.id===U.value}]),title:r.text,onClick:a=>we(r.id)},T(r.text),11,ht))),128))])])):S("",!0)],2)):q.value?(u(),c("iframe",{key:5,ref_key:"htmlFrameRef",ref:K,class:"file-preview-html-frame",sandbox:"allow-scripts allow-same-origin",srcdoc:me.value,onLoad:De},null,40,yt)):(u(),c("pre",bt,T(l.value.content),1))],2)],2)}}}),Lt=Ge(_t,[["__scopeId","data-v-3f3cc931"]]);export{Lt as default};
