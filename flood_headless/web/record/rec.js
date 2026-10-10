// m14 에서 상황판 자동 시연(D)을 headless 크롬으로 돌리며 CDP screencast 프레임을 저장
const {chromium}=require('playwright');const fs=require('fs');
const out=process.argv[2];fs.mkdirSync(out+'/f',{recursive:true});
(async()=>{const b=await chromium.launch({args:['--force-device-scale-factor=1']});
const ctx=await b.newContext({viewport:{width:1920,height:1080},deviceScaleFactor:1});const p=await ctx.newPage();
p.on('pageerror',e=>console.log('ERR',e.message));p.on('console',m=>console.log('C',new Date().toISOString().slice(11,19),m.text()));
await p.goto('http://localhost:8765/?rec');await p.waitForTimeout(4000);
const cdp=await ctx.newCDPSession(p);const idx=[];let n=0;
cdp.on('Page.screencastFrame',async f=>{const name=`f/${String(n++).padStart(6,'0')}.jpg`;fs.writeFileSync(out+'/'+name,Buffer.from(f.data,'base64'));
  idx.push([name,f.metadata.timestamp]);try{await cdp.send('Page.screencastFrameAck',{sessionId:f.sessionId})}catch(e){}});
await cdp.send('Page.startScreencast',{format:'jpeg',quality:92,maxWidth:1920,maxHeight:1080,everyNthFrame:1});
await p.waitForTimeout(2500);
await p.mouse.move(760,58);await p.mouse.click(760,58);await p.waitForTimeout(800);await p.keyboard.press('d');
const t0=Date.now();await p.waitForFunction(()=>document.body.classList.contains('touring'),null,{timeout:10000});
let last=0;while(true){await p.waitForTimeout(2000);
  const on=await p.evaluate(()=>document.body.classList.contains('touring'));
  const m=Math.floor((Date.now()-t0)/60000);{const v=await p.evaluate(()=>document.querySelector('aside a.on span')?.textContent+' | '+document.querySelector('.clk,.onow')?.textContent);if(m!==last){last=m;console.log('min',m,'frames',n,v)}}
  if(!on)break;if(Date.now()-t0>40*60000){console.log('TIMEOUT');break}}
await p.waitForTimeout(3000);await cdp.send('Page.stopScreencast');await p.waitForTimeout(500);
fs.writeFileSync(out+'/idx.json',JSON.stringify(idx));console.log('done frames',n,'sec',(Date.now()-t0)/1000);await b.close()})();
