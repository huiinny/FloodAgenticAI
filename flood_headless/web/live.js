if(/[?&]rec\b/.test(location.search)){const f=()=>document.body.classList.add("rec");document.body?f():document.addEventListener("DOMContentLoaded",f)}
// 실제 에이전트 연결판: 가상 시계 + 감시 자동 실행 + 질문창. 서버(/api/state)를 1초마다 읽는다.
(function(){
const LV={st:null,snapKey:null,lastSig:"",closed:new Set(),drag:false};
TB.blend.none={regions:[],ranked:{stage:"계산 전",top_pct:0,n_top_units:0,n_flagged_regions:0,regions:{},r3h_max_basin_mm:0},obs_mm:0,fc_mm:0,dir:"none_i000000"};
const post=(u,b)=>fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})}).then(()=>poll());
const esc=s=>String(s??"").replace(/&/g,"&amp;").replace(/</g,"&lt;");

// 시점 버튼 자리 -> 가상 시계
seg=function(){
  const s=LV.st,el=document.getElementById("seg");if(!s)return;
  if(!el.dataset.live){el.dataset.live=1;el.classList.add("clock");
    el.innerHTML=`<div class="ops"><span class="dot" id="oDot"></span><b class="onow" id="oNow"></b><span class="osep"></span><div class="osub"><small>자료 수신</small><div id="oSub"></div></div><span class="pill" id="oBusy" hidden>에이전트 판단 중</span></div>
      <div class="ctl"><button id="cPlay" class="cbtn" aria-label="재생"></button>
      <div class="cbox"><b id="cNow"></b><small id="cNext"></small></div>
      <input type="range" id="cSl" min="0" max="150" aria-label="가상 시각">
      <label class="cs">배속 <select id="cSp"><option value="2">×2분/초</option><option value="5">×5분/초</option><option value="10">×10분/초</option><option value="20">×20분/초</option></select></label>
      <label class="cs"><input type="checkbox" id="cAuto"> 자동 실행</label><span class="meta">조작 숨기기: O</span></div>`;
    document.getElementById("cPlay").onclick=()=>post("/api/clock",{action:LV.st.playing?"pause":"play"});
    const sl=document.getElementById("cSl");sl.oninput=()=>{LV.drag=true;document.getElementById("cNow").textContent=tlab(Math.min(+sl.value,143))};
    sl.onchange=()=>{LV.drag=false;const m=+sl.value*10,mm=m%1440;post("/api/clock",{action:"set",now:`${m<1440?"2022-08-08":"2022-08-09"} ${pad(Math.floor(mm/60))}:${pad(mm%60)}`})};
    document.getElementById("cSp").onchange=e=>post("/api/clock",{speed:+e.target.value});
    document.getElementById("cAuto").onchange=e=>post("/api/auto",{on:e.target.checked});
  }
  const wd=["일","월","화","수","목","금","토"],dt=new Date(s.now.replace(" ","T"));
  document.getElementById("oNow").textContent=`${s.now.slice(0,10).replace(/-/g,".")} (${wd[dt.getDay()]}) ${s.now.slice(11)} KST`;
  const rt=tlab(Math.max(0,Math.min(s.n,144)-1));
  document.getElementById("oSub").innerHTML=`<span>레이더 ${rt}</span><span>AWS ${rt}</span><span>하수관 수위계 ${rt}</span><span>수치예보 ${cur.iss}시 발표</span>`;
  document.getElementById("oBusy").hidden=!s.busy;document.getElementById("oDot").className="dot"+(s.busy?" busy":"");
  document.getElementById("cPlay").textContent=s.busy?"판단 중":s.playing?"⏸":"▶";
  document.getElementById("cPlay").disabled=s.busy;
  document.getElementById("cNow").textContent=s.now.slice(5).replace("-","/");
  document.getElementById("cNext").textContent=s.next?`다음 감시 사건 ${s.next.t} · ${s.next.text}`:"남은 감시 사건 없음";
  if(!LV.drag)document.getElementById("cSl").value=Math.min(150,s.n+(s.now>="2022-08-09"?+s.now.slice(14,16)/10:0));
  document.getElementById("cSp").value=String(s.speed);
  document.getElementById("cAuto").checked=s.auto;
};

// 오른쪽: 질문창 + 실행 일지
brief=function(){
  const s=LV.st,rail=document.querySelector(".rail");if(!s)return;
  if(!rail.dataset.live){rail.dataset.live=1;
    rail.innerHTML=`<div class="rblock"><h2>예보관 질문</h2>
      <form id="qf" class="qf"><textarea id="qt" rows="2" placeholder="예: 신대방 쪽 지금 어때? 앞으로는?"></textarea><button class="btn pri" id="qb">질문</button></form>
      <div class="meta" id="qm"></div></div>
      <div class="rblock"><div class="ch"><h2>에이전트 일지</h2><span class="meta" id="rc"></span></div><div id="runs" class="runs"></div></div>`;
    document.getElementById("qf").onsubmit=e=>{e.preventDefault();const q=document.getElementById("qt").value.trim();if(!q||LV.st.busy)return;
      fetch("/api/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({q})}).then(r=>{
        if(r.ok){document.getElementById("qt").value="";poll()}else r.json().then(j=>{document.getElementById("qm").textContent=j.error||"질문을 보내지 못했습니다."})})};
  }
  document.getElementById("qb").disabled=s.busy;
  document.getElementById("qm").textContent=s.busy?"에이전트가 판단 중입니다. 끝나면 질문할 수 있습니다.":"";
  document.getElementById("rc").textContent=`${s.runs.length}회 실행`;
  const box=document.getElementById("runs");
  box.querySelectorAll("details[data-id]").forEach(d=>d.open?LV.closed.delete(d.dataset.id):LV.closed.add(d.dataset.id));
  const sig=JSON.stringify(s.runs)+s.busy;if(sig===LV.sig)return;LV.sig=sig;   // 바뀐 게 없으면 그대로 (읽던 자리·선택 유지)
  box.innerHTML=[...s.runs].reverse().map((r,ix)=>{
    const auto=r.kind==="auto",q=r.q.replace("[자동 실행]","").split("[직전 제안]")[0].trim(),pv=(r.q.split("[직전 제안]")[1]||"").trim();
    const a=r.actions,cls=a&&(a.level==="침수경보"?"p-sev":a.level==="침수예보"?"p-alert":"p-low");
    const dec={approve:"승인됨",edit:"수정 요청됨"}[r.decision]||"";
    return `<article class="run ${r.status}">
      <div class="trig ${auto?"":"ask"}"><b>${r.now.slice(11)} ${auto?"🔔":"❓"}</b><span>${auto?"자동 실행 · ":"예보관 질문 · "}${esc(q.replace(/\s*[^.]*(판단|검증|제안)하라\.?\s*$/,""))}${pv?`<br><span style="opacity:.8">직전 제안 ${esc(pv.split(":")[0])}</span>`:""}</span></div>
      <ol class="acts">${r.lines.map(l=>`<li class="${l.who==="총괄"?"":"sub"}"><span class="who w-${l.who}">${l.who}</span><span><span class="what">${esc(l.what)}</span>${l.result?`<span class="res">${esc(l.result)}</span>`:""}</span></li>`).join("")}
        ${r.status==="running"?`<li class="spin"><span class="who w-총괄">…</span><span class="what">판단 중</span></li>`:""}</ol>
      ${a?`<div class="prop"><div class="lv"><span class="pill ${cls}" style="font-size:14px">${esc(a.level)}</span><b>${esc(a.change)}</b>${dec?`<span class="pill p-low">${dec}</span>`:""}</div><div>${esc(a.level_reason)}</div>
        ${a.field_checks&&a.field_checks.length?`<h4>현장 확인</h4><ul>${a.field_checks.map(f=>`<li><b>${esc(f.place)}</b> · ${esc(f.why)}</li>`).join("")}</ul>`:""}
        ${a.message_draft?`<h4>재난문자 초안</h4><div class="sms">${esc(a.message_draft)}</div>`:""}
        ${a.next_check?`<div class="next">다시 판단: ${esc(a.next_check)}</div>`:""}
        <div class="act"><button class="btn pri" data-d="approve" data-id="${r.id}">승인</button><button class="btn" data-d="edit" data-id="${r.id}">수정</button><button class="btn" data-r="${r.id}">재분석</button></div></div>`:""}
      ${r.status==="error"?`<div class="warn">실행이 끝났지만 답을 받지 못했습니다.</div>`:""}
      ${r.md?`<details data-id="${r.id}" ${LV.closed.has(r.id)?"":"open"}><summary><h2 style="font-size:14px">브리핑</h2><span class="meta">접기·펼치기${r.sec?` · ${r.sec}초`:""}</span></summary><div class="md">${md2html(r.md)}</div></details>`:""}
    </article>`}).join("");
  box.querySelectorAll("button[data-d]").forEach(b=>b.onclick=()=>post("/api/decision",{id:b.dataset.id,decision:b.dataset.d}));
  box.querySelectorAll("button[data-r]").forEach(b=>b.onclick=()=>post("/api/rerun",{id:b.dataset.r}));
};

function apply(s){
  LV.st=s;
  if(s.snap){TB.blend[s.snap_key]=s.snap}
  const key=s.snap_key||"none",b=TB.blend[key]||TB.blend.none;
  const iss=(b.dir.split("_i")[1]||"0000000021").slice(4,6)||"21";
  cur={k:key,n:Math.min(144,s.n),iss:["03","09","15","21"].includes(iss)?iss:"21",final:s.now>="2022-08-09"?1:0};
  if(!TB.blend[cur.k])TB.blend[cur.k]=TB.blend.none;
  const sig=[s.now,s.snap_key,s.playing,s.busy,s.auto,JSON.stringify(s.runs).length,s.runs.map(r=>r.status+r.lines.length+(r.decision||"")).join()].join("|");
  if(sig!==LV.lastSig){LV.lastSig=sig;render()}
}
function poll(){return fetch("/api/state"+(LV.snapKey?`?snap=${encodeURIComponent(LV.snapKey)}`:"")).then(r=>r.json()).then(s=>{if(s.snap)LV.snapKey=s.snap_key;else if(s.snap_key!==LV.snapKey&&s.snap_key){LV.snapKey=null;return poll()}apply(s)}).catch(()=>{})}


// ---- 에이전트가 짚은 곳: 가장 최근 제안의 현장 확인 지점을 수위계·분구 이름과 맞춰 두 지도에 표시
const nz=t=>String(t||"").replace(/\s+/g,"");
const GUN=new Set(GEO.gu.map(f=>f.n.replace(/구$/,"")));
function agentSpots(){const s=LV.st;if(!s)return null;
  const r=[...s.runs].reverse().find(x=>x.actions&&x.actions.field_checks&&x.actions.field_checks.length&&x.now<=s.now);if(!r)return null;
  const txt=r.actions.field_checks.map(f=>f.place).join(" · "),t=nz(txt);
  const gauges=D.sewer.filter(g=>t.includes(nz(g.spot||"")));
  const drains=GEO.drain.filter(f=>{if(GUN.has(f.n))return false;                      // '동작' 같은 구 이름은 분구로 보지 않음
    const re=new RegExp(`(^|[^가-힣0-9])${f.n}(?=$|[^0-9가-힣]|분구)`);return re.test(txt)});
  return {r,gauges,drains}}
function spotLayer(P,W,H,small){const a=agentSpots();if(!a||(!a.gauges.length&&!a.drains.length))return "";const V=css('--violet'),ink=css('--card');
  let g=a.drains.map(f=>`<path d="${pathOf(P,f)}" fill="${V}" fill-opacity=".10" stroke="${V}" stroke-width="2.6" stroke-dasharray="7 4"><title>${f.n} · 에이전트가 짚은 분구</title></path>`).join("");
  if(!small)g+=a.drains.map(f=>{const c=f.c||(()=>{const r=f.r[0];return [r.reduce((s,p)=>s+p[0],0)/r.length,r.reduce((s,p)=>s+p[1],0)/r.length]})();const p=P(c);
    return `<text x="${p[0]}" y="${p[1]}" text-anchor="middle" style="font-family:var(--ui);font-size:12px;font-weight:700;fill:${V};paint-order:stroke;stroke:${ink};stroke-width:4px;stroke-linejoin:round">${f.n}</text>`}).join("");
  g+=a.gauges.map(x=>{const [cx,cy]=P([x.lon,x.lat]);return `<g class="aspot"><circle cx="${cx}" cy="${cy}" r="11" fill="none" stroke="${V}" stroke-width="2.6"><animate attributeName="r" values="10;17;10" dur="2.2s" repeatCount="indefinite"/><animate attributeName="opacity" values="1;.35;1" dur="2.2s" repeatCount="indefinite"/></circle>`+
    (small?"":`<text x="${cx+15}" y="${cy-9}" style="font-family:var(--ui);font-size:12px;font-weight:700;fill:${V};paint-order:stroke;stroke:${ink};stroke-width:4px;stroke-linejoin:round">${shortName(x)}</text>`)+`</g>`}).join("");
  if(small)return g+`<g transform="translate(12,12) scale(1.7)"><rect x="0" y="0" width="116" height="20" rx="10" fill="${ink}" opacity=".95" stroke="${V}" stroke-width="1"/><circle cx="12" cy="10" r="4" fill="none" stroke="${V}" stroke-width="1.8"/><text x="21" y="14" style="font-family:var(--ui);font-size:10.5px;font-weight:700;fill:${V}">에이전트가 짚은 곳</text></g>`;
  g+=`<g transform="translate(${W-12},14)"><rect x="-196" y="0" width="196" height="26" rx="13" fill="${ink}" opacity=".94" stroke="${V}" stroke-width="1.2"/><circle cx="-180" cy="13" r="5" fill="none" stroke="${V}" stroke-width="2.2"/><text x="-168" y="17.5" style="font-family:var(--ui);font-size:12px;font-weight:600;fill:${V}">에이전트가 짚은 곳 · ${a.r.now.slice(11)} ${a.r.kind==="ask"?"질문":"제안"}</text></g>`;
  return g}
window.__spots=agentSpots;   // 점검용
{const _dm=drawMap;drawMap=function(n){_dm(n);const svg=document.getElementById("map");if(svg)svg.insertAdjacentHTML("beforeend",spotLayer(mapP(560,440),560,440,true))}}
{const _rr=renderRisk;renderRisk=function(n){_rr(n);const svg=document.getElementById("riskmap");if(svg)svg.insertAdjacentHTML("beforeend",spotLayer(mapP(560,440),560,440))}}
// ---- 자동 시연: D 키로 시작, Esc 로 멈춤. 녹화 대본 순서대로 탭 전환·재생·승인·질문을 스스로 한다.
const TOUR={on:false};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function until(f,ms=600000){const t0=Date.now();while(TOUR.on&&!f()){if(Date.now()-t0>ms)return;await sleep(500)}}
const lastRun=()=>LV.st&&LV.st.runs[LV.st.runs.length-1];
async function waitAgent(prevCount,hold=true){await until(()=>LV.st.runs.length>prevCount);await railTop();await until(()=>!LV.st.busy);
  if(hold){await post("/api/clock",{action:"pause"});await sleep(400);if(LV.st.playing)await post("/api/clock",{action:"pause"})}await sleep(3500)}   // 끝나자마자 시계 멈춤 (흐르면 다음 장면 시각이 밀림)
// 화면에 보이는 커서: macOS 커서 모양, 사람 손처럼 곡선·감속으로 움직이고, 지나가는 곳에 실제 마우스 이벤트를 보낸다
const CSVG={
  arrow:'<svg width="22" height="32" viewBox="0 0 22 32"><path d="M1.5 1.5v24.2l5.6-5.4 3.7 8.7 4-1.7-3.6-8.6h7.8z" fill="#000" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>',
  hand:'<svg width="26" height="30" viewBox="0 0 26 30" style="margin:-1px 0 0 -7px"><path d="M9.5 2.2c1.1 0 2 .9 2 2v8.3l.1-.6c.2-1 1-1.6 2-1.6s1.9.8 1.9 1.9v.8c.2-.9 1-1.5 1.9-1.5 1.1 0 1.9.8 1.9 1.9v1.1c.2-.8.9-1.3 1.8-1.3 1.1 0 1.9.8 1.9 1.9v6.6c0 4-2.9 7.4-7 7.4h-2.4c-2.4 0-4.2-1-5.6-2.9l-4.8-6.6c-.6-.9-.4-2.1.5-2.7.8-.6 2-.4 2.6.4l1.2 1.6V4.2c0-1.1.9-2 2-2z" fill="#fff" stroke="#000" stroke-width="1.3" stroke-linejoin="round"/><path d="M13.6 19v5M16.9 19v5M20.2 19v5" stroke="#000" stroke-width="1.1" stroke-linecap="round"/></svg>',
  text:'<svg width="12" height="24" viewBox="0 0 12 24" style="margin:-12px 0 0 -6px"><path d="M2 2.5c2 0 3.2.5 4 1.5.8-1 2-1.5 4-1.5M2 21.5c2 0 3.2-.5 4-1.5.8 1 2 1.5 4 1.5M6 4v16M3.6 12h4.8" fill="none" stroke="#fff" stroke-width="3.2" stroke-linecap="round"/><path d="M2 2.5c2 0 3.2.5 4 1.5.8-1 2-1.5 4-1.5M2 21.5c2 0 3.2-.5 4-1.5.8 1 2 1.5 4 1.5M6 4v16M3.6 12h4.8" fill="none" stroke="#000" stroke-width="1.3" stroke-linecap="round"/></svg>'};
const CUR={x:innerWidth*.62,y:innerHeight*.5,el:null,shape:"",over:null,li:null,busy:false,idle:null};
function curEl(){if(!CUR.el){const d=document.createElement("div");d.id="tourCur";document.body.appendChild(d);CUR.el=d}return CUR.el}
function setShape(k){if(CUR.shape!==k){CUR.shape=k;curEl().innerHTML=CSVG[k]}}
function hoverAt(x,y){                                     // 실제 마우스처럼: 아래 요소의 모양·hover·그래프 값 상자
  const t=document.elementFromPoint(x,y);if(!t)return;
  setShape(t.closest("textarea,input")?"text":t.closest("a,button,.list li,label,[role=button]")?"hand":"arrow");
  const li=t.closest(".list li");if(li!==CUR.li){CUR.li?.classList.remove("fh");li?.classList.add("fh");CUR.li=li}
  const sv=t.closest("#rain,#sew");if(CUR.over&&CUR.over!==sv)CUR.over.dispatchEvent(new MouseEvent("mouseleave"));CUR.over=sv;
  if(sv)sv.dispatchEvent(new MouseEvent("mousemove",{bubbles:true,clientX:x,clientY:y}))}
function place(x,y){if(!TOUR.on)return;CUR.x=x;CUR.y=y;const d=curEl();d.style.display="block";d.style.transform=`translate(${x}px,${y}px)`;hoverAt(x,y)}
const rnd=(a,b)=>a+Math.random()*(b-a);
function glide(x,y,ms,bend=true){return new Promise(res=>{const x0=CUR.x,y0=CUR.y,dx=x-x0,dy=y-y0,dist=Math.hypot(dx,dy)||1;
  const off=bend?rnd(-.18,.18)*Math.min(dist,500):0,cx=x0+dx*.5-dy/dist*off,cy=y0+dy*.5+dx/dist*off;   // 손목이 그리는 완만한 곡선
  const t0=performance.now();const ease=u=>u<.5?4*u*u*u:1-Math.pow(-2*u+2,3)/2;
  const step=now=>{const u=Math.min(1,(now-t0)/ms),e=ease(u),a=(1-e)*(1-e),b2=2*(1-e)*e,c=e*e;
    place(a*x0+b2*cx+c*x,a*y0+b2*cy+c*y);if(u<1&&TOUR.on)setTimeout(()=>step(performance.now()),16);else res()};step(performance.now())})}
async function moveTo(x,y,ms){CUR.busy=true;const d=Math.hypot(x-CUR.x,y-CUR.y);
  const T=ms??Math.min(1300,260+140*Math.log2(d/12+1))*rnd(.9,1.15);
  if(d>160&&ms==null){const ox=(x-CUR.x)/d*rnd(4,10),oy=(y-CUR.y)/d*rnd(4,10);await glide(x+ox,y+oy,T*.88);await glide(x,y,T*.22,false)}   // 살짝 지나쳤다 돌아옴
  else await glide(x,y,T);CUR.busy=false}
function idleLoop(){clearTimeout(CUR.idle);if(!TOUR.on)return;                // 기다리는 동안 손이 조금씩 움직임
  CUR.idle=setTimeout(async()=>{if(TOUR.on&&!CUR.busy&&!document.querySelector("textarea:focus")){CUR.busy=true;
    await glide(Math.max(220,Math.min(innerWidth-30,CUR.x+rnd(-40,40))),Math.max(30,Math.min(innerHeight-30,CUR.y+rnd(-28,28))),rnd(500,1100));CUR.busy=false}idleLoop()},rnd(2500,6000))}
function tourEnd(){post("/api/clock",{action:"hold",on:false});document.body.classList.remove("touring");clearTimeout(CUR.idle);CUR.li?.classList.remove("fh");CUR.li=null;
  if(CUR.over){CUR.over.dispatchEvent(new MouseEvent("mouseleave"));CUR.over=null}if(CUR.el)CUR.el.style.display="none"}
document.addEventListener("mousemove",e=>{if(!TOUR.on&&e.isTrusted){CUR.x=e.clientX;CUR.y=e.clientY}});   // 시작할 때 진짜 커서 자리에서 이어받음
function press(){const d=curEl();d.style.transition="scale .08s";d.style.scale=".9";setTimeout(()=>d.style.scale="1",110)}
async function pointAt(el){if(!el)return;const r=el.getBoundingClientRect();
  await moveTo(r.left+r.width*rnd(.38,.62),r.top+r.height*rnd(.35,.65));await sleep(rnd(120,320))}
async function clickEl(sel){const el=typeof sel==="string"?document.querySelector(sel):sel;if(!el)return false;
  await pointAt(el);press();await sleep(90);(typeof sel==="string"&&document.querySelector(sel)||el).click();return true}
async function sweep(id,f0,f1,ms){const el=document.getElementById(id);if(!el)return;const r=el.getBoundingClientRect(),y=r.top+r.height*rnd(.38,.5);
  await moveTo(r.left+r.width*f0,y);await sleep(400);CUR.busy=true;
  const N=5;for(let k=1;k<=N&&TOUR.on;k++){await glide(r.left+r.width*(f0+(f1-f0)*k/N),y+rnd(-6,6),ms/N*rnd(.7,1.3));await sleep(rnd(80,350))}
  await sleep(1400);CUR.busy=false;await moveTo(r.left+r.width*f1+rnd(20,60),r.top-rnd(25,45))}
function glideScroll(sc,dy,ms){return new Promise(res=>{const y0=sc.scrollTop,t0=performance.now(),e=u=>u<.5?2*u*u:1-Math.pow(-2*u+2,2)/2;
  const st=()=>{const u=Math.min(1,(performance.now()-t0)/ms);sc.scrollTop=y0+dy*e(u);if(u<1&&TOUR.on)setTimeout(st,16);else res()};st()})}
async function readBrief(id,secs=14){                     // 새 카드의 브리핑을 위에서부터 천천히 읽어 내려감
  const d=document.querySelector(`details[data-id="${id}"]`);if(!d)return;
  let sc=d.parentElement;while(sc&&sc!==document.body&&!(sc.scrollHeight>sc.clientHeight+4&&/auto|scroll/.test(getComputedStyle(sc).overflowY)))sc=sc.parentElement;
  if(!sc||sc===document.body)sc=document.scrollingElement;
  const box=sc===document.scrollingElement?{left:0,top:0,width:innerWidth,height:innerHeight}:sc.getBoundingClientRect();
  await moveTo(box.left+box.width*rnd(.45,.6),box.top+Math.min(box.height,innerHeight)*rnd(.45,.6));
  const top=d.getBoundingClientRect().top-box.top-16;await glideScroll(sc,top,1400);await sleep(700);
  const end=Date.now()+secs*1000;while(TOUR.on&&Date.now()<end){const before=sc.scrollTop;
    await glideScroll(sc,rnd(110,170),rnd(900,1300));await sleep(rnd(500,1100));if(sc.scrollTop===before)break}
  await sleep(2000)}
async function view(v,ms){const a=document.querySelector(`aside a[data-v="${v}"]`);if(a){await pointAt(a);press();await sleep(90)}setView(v);await sleep(ms)}
async function scrollMain(steps,dy,ms){const m=document.querySelector("main");{const r=m.getBoundingClientRect();await moveTo(r.left+r.width*.55,r.top+r.height*.6)}for(let i=0;i<steps&&TOUR.on;i++){const d=dy<0?-m.scrollTop:dy;await glideScroll(m,d,Math.min(1800,500+Math.abs(d)*1.6));await sleep(ms-600)}}
async function jump(now){await railTop();await post("/api/auto",{on:true});await post("/api/clock",{action:"set",now});await sleep(2500)}
async function runScene(now){const c=LV.st.runs.length;await post("/api/clock",{action:"play"});await waitAgent(c);if(LV.st.playing)await post("/api/clock",{action:"pause"})}
// ---- 카드 읽기: 제안 카드를 짚고, 브리핑의 절을 찾아 사람이 드래그하듯 문장을 선택하며 읽음
const RAIL=()=>document.querySelector(".rail");
const artOf=id=>document.querySelector(`details[data-id="${id}"]`)?.closest("article")||[...document.querySelectorAll("#runs article")].find(a=>a.querySelector(`[data-id="${id}"]`));
async function railTop(){const sc=RAIL();if(sc&&sc.scrollTop>4)await glideScroll(sc,-sc.scrollTop,Math.min(1400,500+sc.scrollTop*.4))}
async function railTo(el,off=110){const sc=RAIL();if(!el||!sc)return;const r=el.getBoundingClientRect(),b=sc.getBoundingClientRect();
  const dy=r.top-b.top-off;if(Math.abs(dy)>8)await glideScroll(sc,dy,Math.min(1600,500+Math.abs(dy)*.9));await sleep(250)}
function textNodes(el){const w=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),o=[];let n;while(n=w.nextNode())if(n.nodeValue.trim())o.push(n);return o}
async function dragSelect(el,ms=1300){if(!el||!TOUR.on)return;                 // 형광펜: 문장에 노란 밑줄 칠이 왼쪽부터 번짐
  const sp=document.createElement("span");sp.className="tourMark";while(el.firstChild)sp.appendChild(el.firstChild);el.appendChild(sp);
  const rs=sp.getClientRects(),f=rs[0],l=rs[rs.length-1];if(!f)return;
  await moveTo(f.left+4,f.bottom-3);await sleep(250);sp.style.transitionDuration=ms+"ms";sp.offsetWidth;sp.classList.add("on");
  await moveTo(Math.min(f.right,f.left+260),f.bottom-3,Math.min(ms,1200));await sleep(Math.max(0,ms-1100))}
const clearSel=()=>document.querySelectorAll("span.tourMark").forEach(sp=>{const p=sp.parentNode;while(sp.firstChild)p.insertBefore(sp.firstChild,sp);sp.remove()});
function secOf(art,title){const hs=[...art.querySelectorAll(".md h1,.md h2,.md h3,.md h4")];return hs.find(h=>h.textContent.trim().startsWith(title))}
function bodyAfter(h){let e=h.nextElementSibling;while(e&&!/^(P|UL|OL|DIV)$/.test(e.tagName))e=e.nextElementSibling;
  if(e&&/^(UL|OL)$/.test(e.tagName))e=e.querySelector("li")||e;return e}
async function showProp(id,full=true){const a=artOf(id),p=a?.querySelector(".prop");if(!p)return;
  await railTo(p,90);await pointAt(p.querySelector(".pill"));await sleep(1600);
  const why=p.querySelector(".lv")?.nextElementSibling;if(why){await dragSelect(why,1400);await sleep(1800);clearSel()}
  if(!full)return;
  for(const li of [...p.querySelectorAll("ul li")].slice(0,3)){if(!TOUR.on)break;await railTo(li,200);await pointAt(li.querySelector("b")||li);await sleep(1500)}
  const sms=p.querySelector(".sms");if(sms&&TOUR.on){await railTo(sms,220);await dragSelect(sms,1600);await sleep(2200);clearSel()}}
async function readSec(id,title,hold=3200){const a=artOf(id),h=a&&secOf(a,title);if(!h||!TOUR.on)return;
  await railTo(h,90);await pointAt(h);await sleep(500);const b=bodyAfter(h);if(b){await dragSelect(b,1500);await sleep(hold);clearSel()}}
async function readWord(id,word,n=2,hold=3000){const a=artOf(id);if(!a)return;
  const els=[...a.querySelectorAll(".md li,.md p")].filter(e=>e.textContent.includes(word)&&!e.querySelector("li"));
  for(const e of els.slice(0,n)){if(!TOUR.on)break;await railTo(e,160);await dragSelect(e,1500);await sleep(hold);clearSel()}}
async function pointLine(id,word){const a=artOf(id),li=a&&[...a.querySelectorAll(".acts li")].find(l=>l.textContent.includes(word));if(li){await railTo(li,160);await pointAt(li.querySelector(".res")||li);await sleep(1800)}}
window.__tour={showProp,readSec,readWord,pointLine,dragSelect,on:v=>{TOUR.on=v;if(v){document.body.classList.add("touring");place(CUR.x,CUR.y)}}};   // 점검용
async function tour(){
  TOUR.on=true;const go=()=>TOUR.on;await post("/api/clock",{action:"hold",on:true});document.body.classList.add("touring");place(CUR.x,CUR.y);idleLoop();
  // 0. 03:55 훑어보기
  await jump("2022-08-08 03:55");await view("board",5000);
  if(go())await view("risk",3500);if(go()){await clickEl('#riskSrc button[data-l="terrain"]');await sleep(2500);await clickEl('#riskSrc button[data-l="terrain"]');await sleep(2500)}
  if(go()){await view("radar",1500);await clickEl("#play");await sleep(5000)}
  if(go())await view("board",3000);
  // 1. 04:00 예보 도착 자동 실행 → 제안 카드(짧게)·한 줄 판단
  if(go()){await runScene();const r=lastRun();if(r&&go()){await showProp(r.id,false);await readSec(r.id,"한 줄 판단");await railTop()}}
  // 2. 13:10 침수예보 기준 → 기다리는 동안 상황판 둘러보기 → 제안 카드·브리핑 → 위험 지도·레이더
  if(go()){await jump("2022-08-08 13:05");const c=LV.st.runs.length;await post("/api/clock",{action:"play"});await until(()=>LV.st.busy);
    for(const v of ["radar","aws","all"]){if(!go())break;await clickEl(`#onlyRain button[data-v="${v}"]`);await sleep(3000)}
    if(go()){await clickEl("#list li");await sleep(3000)}
    if(go())await sweep("rain",.42,.58,4000);
    await waitAgent(c);const r=lastRun();
    if(r&&go()){await showProp(r.id);await readSec(r.id,"한 줄 판단");await readSec(r.id,"관측과 예측");await railTop()}
    if(go())await view("risk",6000);if(go()){await view("radar",1200);await clickEl("#play");await sleep(6000)}if(go())await view("board",2500)}
  // 3. 20:40 침수경보 기준 → 제안 카드(현장 확인·재난문자) → 승인 → 브리핑 → 위험 지도
  let r40=null;
  if(go()){await jump("2022-08-08 20:35");await runScene();await sleep(800);r40=lastRun();
    if(r40&&go()){await showProp(r40.id);
      const b=document.querySelector(`button[data-d="approve"][data-id="${r40.id}"]`);if(b){await railTo(b,320);await clickEl(`button[data-d="approve"][data-id="${r40.id}"]`);await sleep(2500)}
      await readSec(r40.id,"한 줄 판단");await readSec(r40.id,"관측과 예측",3800);await railTop()}
    if(go())await view("board",2000);if(go())await sweep("rain",.75,.87,3500);
    if(go())await view("risk",6000);if(go())await view("board",2500)}
  // 4. 예보관 질문: 20:40 카드에서 '예측이 놓친 곳'으로 나온 신대방을 짚고 → 묻기 → 신대방에 맞춘 활동·답 확인
  if(go()){if(r40){const a=artOf(r40.id),li=a&&[...a.querySelectorAll(".prop li,.md li")].find(e=>e.textContent.includes("신대방"));if(li){await railTo(li,200);await dragSelect(li,1400);await sleep(2500);clearSel()}}
    await glideScroll(RAIL(),-RAIL().scrollTop,1200);
    const q="신대방 쪽 지금 어때? 앞으로는?",ta=document.getElementById("qt");await clickEl(ta);ta.focus();ta.value="";
    for(const ch of q){if(!go())break;ta.value+=ch;await sleep(ch===" "?rnd(140,260):rnd(55,150))}await sleep(rnd(400,700));ta.blur();await sleep(800);
    const c=LV.st.runs.length;await clickEl("#qb");await until(()=>LV.st.runs.length>c);
    await until(()=>{const r=lastRun();return !LV.st.busy||(r&&r.lines.some(l=>l.who!=="총괄"))});const rq=lastRun();
    if(rq&&go())await pointLine(rq.id,"신대방");                         // 총괄이 '신대방 …'으로 일을 나눠 맡기는 줄
    await waitAgent(c);const r=lastRun();
    if(r&&go()){await showProp(r.id,false);await readSec(r.id,"한 줄 판단");await readWord(r.id,"신대방",2);await readSec(r.id,"관측과 예측");await railTop()}
    if(go()){await view("risk",1500);for(const el of [...document.querySelectorAll("#riskmap .aspot")].slice(0,2)){if(!go())break;await pointAt(el.querySelector("circle"));await sleep(2200)}await sleep(3000);if(go())await view("board",2000)}}
  // 5. 22:00 21시 예보 도착 → 직전 승인 이어받음
  if(go()){await jump("2022-08-08 21:55");await runScene();const r=lastRun();if(r&&go()){await showProp(r.id,false);await readSec(r.id,"한 줄 판단");await railTop()}}
  // 6. 검증
  if(go()){await view("verify",2500);await scrollMain(4,320,3500);await scrollMain(1,-5000,1500)}
  TOUR.on=false;tourEnd();
}
document.addEventListener("keydown",e=>{if(/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)||window.VIEW_ONLY)return;
  if((e.code==="KeyD"||e.key==="d"||e.key==="D"||e.key==="ㅇ")&&!TOUR.on){e.preventDefault();tour()}
  if(e.key==="Escape"&&TOUR.on){TOUR.on=false;tourEnd()}});

window._noAnim=1;
if(window.VIEW_ONLY){document.body.classList.add("viewonly")}
if(/[?&]op=1/.test(location.search))document.body.classList.add("op");
function toast(msg){let el=document.getElementById("kToast");if(!el){el=document.createElement("div");el.id="kToast";el.className="ktoast";document.body.appendChild(el)}
  el.textContent=msg;el.classList.add("on");clearTimeout(window._kt);window._kt=setTimeout(()=>el.classList.remove("on"),1800)}
document.addEventListener("keydown",e=>{if(/INPUT|TEXTAREA|SELECT/.test(e.target.tagName))return;const s=LV.st;if(!s)return;
  const k=({KeyN:"n",KeyO:"o"})[e.code]||e.key.toLowerCase();if(![" ","n","o","1","2","3","4","5"].includes(k))return;e.preventDefault();
  const JUMP={"1":"2022-08-08 03:55","2":"2022-08-08 13:05","3":"2022-08-08 20:35","4":"2022-08-08 21:55","5":"2022-08-09 00:25"};
  if(JUMP[k]&&!window.VIEW_ONLY){if(s.busy){toast("에이전트가 판단 중입니다. 끝나면 다시 흐릅니다.");return}
    post("/api/auto",{on:true}).then(()=>post("/api/clock",{action:"set",now:JUMP[k]}));return}
  if(window.VIEW_ONLY){toast("보기 전용 화면입니다. 조작은 m14 (localhost:8765) 화면에서만 됩니다.");return}
  if(k==="o"){document.body.classList.toggle("op");return}
  if(s.busy){toast("에이전트가 판단 중입니다. 끝나면 다시 흐릅니다.");return}
  const atEnd=s.now>="2022-08-09 00:30";
  if(k===" "){if(atEnd){toast("하루가 끝났습니다. O 로 슬라이더를 열어 시각을 옮기거나 run_server.sh prep 으로 처음부터");return}
    post("/api/clock",{action:s.playing?"pause":"play"});return}
  if(k==="n"){if(!s.next){toast("남은 감시 사건이 없습니다. O 로 시각을 옮기거나 run_server.sh prep 으로 처음부터");return}
    if(!s.auto){post("/api/auto",{on:true})}
    const [h,m]=s.next.t.split(":").map(Number),day=s.now.slice(0,10),tt=h*60+m-3;
    const nd=s.next.t<s.now.slice(11)&&day==="2022-08-08"?"2022-08-09":day;
    post("/api/clock",{action:"set",now:`${nd} ${pad(Math.floor(((tt%1440)+1440)%1440/60))}:${pad(((tt%60)+60)%60)}`}).then(()=>post("/api/clock",{action:"play"}));
}});
poll();setInterval(poll,1000);
})();
