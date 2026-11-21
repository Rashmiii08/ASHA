const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const baseURL = process.env.FIELD_REGISTER_TEST_URL || 'http://127.0.0.1:8765';
(async () => {
 const browser = await chromium.launch({executablePath: process.env.CHROMIUM_EXECUTABLE});
 try {
 const context = await browser.newContext({ viewport: {width:1440,height:900}});
 const page = await context.newPage();
 const pageErrors=[];
 page.on('pageerror', error=>pageErrors.push(error.message));
 await page.goto(baseURL);
 await page.getByRole('button',{name:'Start session'}).focus();
 await page.keyboard.press('Enter');
 await page.locator('#recent').getByText('No confirmed visits yet.').waitFor();
 await page.getByRole('link',{name:'Start voice visit'}).click();
 await page.getByRole('button',{name:'Start voice visit'}).click();
 await page.getByText('I am an AI assistant, not a human.').waitFor();
 await page.getByRole('button',{name:'Use sample'}).click();
 await page.getByRole('button',{name:'Send transcript'}).click();
 await page.getByRole('alert').getByText(/Danger sign detected/).waitFor();
 await page.evaluate(()=>navigator.serviceWorker.ready);
 await context.setOffline(true);
 await page.reload();
 await page.getByText('Offline · voice needs a connection').waitFor();
 await page.getByRole('alert').getByText(/Danger sign detected/).waitFor();
 await context.setOffline(false);
 await page.waitForFunction(() => document.querySelector('#network').textContent.trim() === 'Connected', undefined, {timeout:20000});
 await page.getByRole('button',{name:'Start voice visit'}).click();
 await page.locator('#phase').getByText('Review and confirm').waitFor();
 await page.locator('#demo-text').fill('No, temperature is 36.9.');
 await page.getByRole('button',{name:'Send transcript'}).click();
 await page.locator('#details').getByText('36.9').waitFor();
 assert.equal(await page.getByRole('alert').count(),0);
 await page.getByRole('button',{name:'Confirm and save'}).click();
 await page.locator('#draft-label').getByText('Saved').waitFor();
 await page.getByRole('link',{name:'Visit history'}).click();
 await page.locator('#records').getByText('DEMO-4821').waitFor();
 await page.getByRole('searchbox').fill('not present');
 await page.getByText('No visits match your search.').waitFor();
 await page.getByRole('link',{name:'Follow-ups'}).click();
 await page.getByRole('button',{name:/Mark follow-up complete/}).click();
 await page.getByText('Completed',{exact:true}).waitFor();
 await page.setViewportSize({width:360,height:740});
 await page.getByRole('link',{name:'Settings'}).click();
 await page.locator('#language').selectOption('hi');
 await page.getByRole('link',{name:'Voice visit'}).click();
 await page.getByRole('button',{name:'Start voice visit'}).click();
 await page.getByText(/मैं एक एआई सहायक हूँ/).waitFor();
 await page.getByRole('button',{name:'Use sample'}).click();
 await page.getByRole('button',{name:'Send transcript'}).click();
 await page.getByRole('alert').getByText(/खतरे का संकेत/).waitFor();
 await page.locator('#demo-text').fill('लाभार्थी आईडी ASHA-123456789012345678901234567890123456789012345678901234567890');
 await page.getByRole('button',{name:'Send transcript'}).click();
 await page.locator('#details').getByText(/^ASHA-1234/).waitFor();
 assert.equal(await page.evaluate(()=>document.body.scrollWidth),360);
 assert.deepEqual(pageErrors,[]);
 await context.close();
 const denied = await browser.newContext();
 await denied.addInitScript(()=> {
   navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Test denial', 'NotAllowedError'); };
 });
 await denied.route('**/api/status', route=>route.fulfill({json:{mode:'real',voice_ready:true,voice_error:null}}));
 const deniedPage = await denied.newPage();
 await deniedPage.goto(baseURL + '/#live');
 await deniedPage.getByRole('button',{name:'Start voice visit'}).click();
 await deniedPage.getByText(/Microphone permission was denied/).waitFor();
 await denied.close();
 const capture = await browser.newContext({serviceWorkers: 'block'});
 await capture.route('**/api/status', route=>route.fulfill({json:{mode:'real',voice_ready:true,voice_error:null}}));
 await capture.addInitScript(() => {
   Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {value: async () => {
     const audioContext = new AudioContext();
     await audioContext.resume();
     const signal = audioContext.createOscillator();
     const output = audioContext.createMediaStreamDestination();
     signal.connect(output);
     signal.start();
     return output.stream;
   }});
 });
 await capture.routeWebSocket('**/ws', ws => {
   ws.onMessage(raw => {
     const message = JSON.parse(raw);
     if (message.type === 'hello') ws.send(JSON.stringify({type:'ready', session:'synthetic', mode:'real'}));
     if (message.type === 'audio') {
       const bytes = Buffer.from(message.data, 'base64');
       assert.equal(bytes.length, 3200);
       assert.ok(bytes.some(byte => byte !== 0));
       ws.send(JSON.stringify({type:'transcript', text:'Synthetic microphone test', final:true}));
     }
   });
 });
 const capturePage = await capture.newPage();
 await capturePage.goto(baseURL + '/#live');
 await capturePage.getByRole('button',{name:'Start voice visit'}).click();
 await capturePage.locator('#transcript').getByText('Synthetic microphone test').waitFor({timeout:10000});
 await capturePage.getByRole('button',{name:'Stop session'}).click();
 await capture.close();
 console.log('Browser smoke passed: keyboard entry, mobile width, Hindi, danger, correction, offline draft, reconnect, confirm, history, follow-up, simulated permission denial, synthetic microphone PCM over WebSocket.');
 } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
