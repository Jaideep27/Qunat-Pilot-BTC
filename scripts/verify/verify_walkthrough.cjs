// Decode the exported video in Chrome and preserve a frame for visual review.
const {chromium}=require('../../frontend/node_modules/playwright');
const fs=require('node:fs');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const assert=require('node:assert/strict');
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    await page.goto(pathToFileURL(path.resolve('docs/walkthrough/quantpilot-btc.webm')).href);
    const metadata=await page.locator('video').evaluate(async video=>{
      if(video.readyState<1)await new Promise(resolve=>video.addEventListener('loadedmetadata',resolve,{once:true}));
      video.pause();
      if(!Number.isFinite(video.duration)){
        video.currentTime=1e10;
        await new Promise(resolve=>video.addEventListener('seeked',resolve,{once:true}));
      }
      const duration=video.duration;
      video.currentTime=55;
      await new Promise(resolve=>video.addEventListener('seeked',resolve,{once:true}));
      video.removeAttribute('controls');
      return {duration_seconds:duration,width:video.videoWidth,height:video.videoHeight};
    },undefined,{timeout:30000});
    assert(metadata.duration_seconds>=119&&metadata.duration_seconds<=125);
    assert.equal(metadata.width,1440);assert.equal(metadata.height,1000);
    await page.screenshot({path:'docs/walkthrough/decoded-frame.png'});
    fs.writeFileSync('docs/walkthrough/verification.json',JSON.stringify({status:'passed',...metadata,
      bytes:fs.statSync('docs/walkthrough/quantpilot-btc.webm').size},null,2));
    console.log(JSON.stringify(metadata));
  } finally {await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
