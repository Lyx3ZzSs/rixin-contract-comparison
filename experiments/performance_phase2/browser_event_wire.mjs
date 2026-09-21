// Run only after the main browser matrix and backend timing experiments finish.
// Serve event-wire/ on loopback port 4181. No app endpoint is modified.
const fs = await import('node:fs/promises');
const root = '/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison';
const task = await taskSpace(4);
const page = task.page('p1');
await page.goto('http://127.0.0.1:4181/');
await page.cdp('Performance.enable');
const rows=[];
for(const label of ['A','B','C','D','E']) {
  for(let repeat=1;repeat<=3;repeat++) {
    for(const variant of ['baseline','candidate']) {
      const before=await page.cdp('Performance.getMetrics');
      const result=await page.evaluate(async ({label,variant})=>{
        const start=performance.now();
        const response=await fetch(`/${label}-${variant}.json`,{cache:'no-store'});
        if(!response.ok)throw new Error('Fixture fetch failed');
        const raw=await response.text();const received=performance.now();
        let value=JSON.parse(raw);const parsed=performance.now();
        if(variant==='candidate') {
          const evidence=value.evidence_by_id;
          const restore=diff=>{
            const result={...diff};
            for(const side of ['original','compare']) {
              const key=side+'_evidence_refs';
              if(key in result) {
                result[side+'_evidence']=result[key].map(id=>{
                  if(!(id in evidence))throw new Error('Unresolved evidence');
                  return structuredClone(evidence[id]);
                });
                delete result[key];
              }
            }
            return result;
          };
          value={...value,events:value.events.map(e=>({...e,diff:restore(e.diff),children:e.children.map(restore)}))};
          delete value.evidence_by_id;delete value.wire_version;
        }
        const ready=performance.now();
        // Retain only the last materialized response, as the actual state does.
        window.__wireResult=value;
        return {fetch_ms:received-start,json_parse_ms:parsed-received,hydrate_ms:ready-parsed,
                ready_ms:ready-start,bytes:new TextEncoder().encode(raw).length,event_count:value.event_count,exact_equal:true};
      },{label,variant});
      const after=await page.cdp('Performance.getMetrics');
      // Verification allocations are excluded from the timing/heap observation.
      await page.evaluate(async variant=>{
        const canonical=x=>Array.isArray(x)?x.map(canonical):x&&typeof x==='object'?Object.fromEntries(Object.keys(x).sort().map(k=>[k,canonical(x[k])])):x;
        const encoded=new TextEncoder().encode(JSON.stringify(canonical(window.__wireResult)));
        const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',encoded))).join(',');
        if(variant==='baseline')window.__wireExpected=digest;
        else if(digest!==window.__wireExpected)throw new Error('Browser hydration changed response semantics');
      },variant);
      const heap=x=>x.metrics.find(m=>m.name==='JSHeapUsedSize')?.value;
      rows.push({case:label,repeat,variant,...result,heap_before:heap(before),heap_after:heap(after),heap_delta:heap(after)-heap(before)});
      console.log(rows.at(-1));
    }
  }
}
await fs.writeFile(root+'/output/analysis/perf-phase2-20260921/event-wire/browser.json',JSON.stringify(rows,null,2));
// Keep TaskSpace open for final app verification; root owner finishes it once.
