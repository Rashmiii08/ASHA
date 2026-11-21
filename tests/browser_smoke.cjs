const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const {signIn} = require('./browser_auth_support.cjs');
const baseURL = process.env.FIELD_REGISTER_TEST_URL || 'https://localhost:8766';
(async () => {
 const browser = await chromium.launch({executablePath: process.env.CHROMIUM_EXECUTABLE, args: ["--ignore-certificate-errors"]});
 try {
 const context = await browser.newContext({ignoreHTTPSErrors:true, viewport: {width:1440,height:900}});
 await signIn(context, baseURL);
 await context.addInitScript(() => {
   navigator.geolocation.getCurrentPosition = (_success, error) => error({code:1});
 });
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
 await page.waitForFunction(async () => Boolean((await navigator.serviceWorker.getRegistration())?.active), undefined, {timeout:15000});
 await context.setOffline(true);
 await page.reload();
 await page.locator('#account').getByRole('heading',{name:'Sign in'}).waitFor();
 assert.equal(await page.getByRole('alert').count(),0);
 await context.setOffline(false);
 await page.waitForFunction(() => document.querySelector('#network').textContent.trim() === 'Connected', undefined, {timeout:20000});
 await page.getByRole('button',{name:'Start voice visit'}).click();
 await page.locator('#phase').getByText('Review and confirm').waitFor();
 await page.getByRole('alert').getByText(/Danger sign detected/).waitFor();
 await page.locator('#demo-text').fill('No, temperature is 36.9.');
 await page.getByRole('button',{name:'Send transcript'}).click();
 await page.locator('#details').getByText('36.9').waitFor();
 assert.equal(await page.getByRole('alert').count(),0);
 await page.getByRole('button',{name:'Stop session'}).click();
 await page.locator('#transcript').getByText('No, temperature is 36.9.').waitFor();
 await page.locator('#details').getByText('36.9').waitFor();
 assert.deepEqual(await page.evaluate(()=>fetch('/api/visits').then(r=>r.json())), []);
 await page.getByRole('button',{name:'Save Visit'}).click();
 await page.locator('#draft-label').getByText('Saved').waitFor();
 await page.getByRole('link',{name:'Visit history'}).click();
 await page.locator('#records').getByText('4821').waitFor();
 await page.getByRole('searchbox').fill('not present');
 await page.getByText('No visits match your search.').waitFor();
 await page.getByRole('link',{name:'Follow-ups'}).click();
 await page.getByRole('button',{name:/Mark follow-up complete/}).click();
 await page.getByText('Completed',{exact:true}).waitFor();
 await page.getByRole('link',{name:'Voice visit'}).click();
 await page.getByRole('button',{name:'Start voice visit'}).click();
 const general = 'This was a general health visit. The patient has sugar and high BP.';
 await page.locator('#demo-text').fill(general);
 await page.getByRole('button',{name:'Send transcript'}).click();
 await page.locator('#transcript').getByText(general).waitFor();
 await page.locator('#details').getByText('General health visit').waitFor();
 await page.locator('#reported-conditions').getByText('Diabetes (patient-reported)').waitFor();
 await page.locator('#reported-conditions').getByText('Hypertension (patient-reported)').waitFor();
 await page.locator('#demo-text').fill('Polio vaccination is due in 2 weeks.');
 await page.getByRole('button',{name:'Send transcript'}).click();
 await page.locator('#vaccination-plans').getByText(/POLIO.*Follow-up reminder/).waitFor();
 await page.getByRole('button',{name:'Stop session'}).click();
 await page.getByText('Edit visit details',{exact:true}).click();
 await page.locator('#edit-beneficiary').fill('7001');
 await page.getByRole('checkbox',{name:'Hypertension'}).uncheck();
 await page.getByLabel('Previous diagnosis for Diabetes').selectOption('no');
 await page.getByLabel('Weight (kg)').fill('62.4');
 await page.getByLabel('Transcript correction').fill('Synthetic corrected transcript.');
 await page.getByRole('button',{name:'Apply draft changes'}).click();
 await page.locator('#reported-conditions').getByText('Hypertension (patient-reported)').waitFor({state:'hidden'});
 await page.getByRole('button',{name:'Save Visit'}).click();
 await page.getByText('Visit saved successfully. You can find it in Visit history.').waitFor();
 await page.getByRole('link',{name:'Visit history'}).click();
 await page.locator('#search').fill('General health visit');
 await page.locator('#records').getByText('7001').waitFor();
 await page.getByRole('link',{name:'Follow-ups'}).click();
 const vaccination = page.locator('#followup-list article').filter({hasText:'POLIO vaccination'});
 await vaccination.getByText('7001').waitFor();
 await vaccination.getByRole('button',{name:/Mark follow-up complete/}).click();
 await vaccination.getByText('Completed',{exact:true}).waitFor();
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
 await page.locator('#assistant').getByText(/exactly four numeric digits/).waitFor();
 assert.equal(await page.evaluate(()=>document.body.scrollWidth),360);
 assert.deepEqual(pageErrors,[]);
 await context.close();
 const denied = await browser.newContext({ignoreHTTPSErrors:true});
 await signIn(denied, baseURL);
 await denied.addInitScript(()=> {
   navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Test denial', 'NotAllowedError'); };
 });
 await denied.route('**/api/status', route=>route.fulfill({json:{mode:'real',voice_ready:true,voice_error:null}}));
 const deniedPage = await denied.newPage();
 await deniedPage.goto(baseURL + '/#live');
 await deniedPage.getByRole('button',{name:'Start voice visit'}).click();
 await deniedPage.getByText(/Microphone permission was denied/).waitFor();
 await denied.close();
 const capture = await browser.newContext({ignoreHTTPSErrors:true, serviceWorkers: 'block', isMobile: true, hasTouch: true, viewport:{width:390,height:844}});
 await signIn(capture, baseURL);
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
 let voiceFrames = 0, finishRequests = 0, displayedSequence = 0;
 await capture.routeWebSocket('**/ws', ws => {
   ws.onMessage(raw => {
     const message = JSON.parse(raw);
     if (message.type === 'hello') ws.send(JSON.stringify({type:'ready', session:'synthetic', mode:'real', resumed:!!message.session}));
     if (message.type === 'audio') {
       const bytes = Buffer.from(message.data, 'base64');
       assert.equal(bytes.length, 1024);
       if (bytes.some(byte => byte !== 0)) {
         voiceFrames += 1;
         if (voiceFrames === 1) {
           ws.send(JSON.stringify({type:'transcript', text:'Synthetic partial text', final:false}));
         }
       }
     }
     if (message.type === 'transcript_displayed') displayedSequence = message.sequence;
     if (message.type === 'heartbeat') {
       ws.send(JSON.stringify({type:'heartbeat', audio_chunks:voiceFrames, prisma:{
         frames:voiceFrames, nonzero_frames:voiceFrames, peak_dbfs:-12,
         speech_starts:1, processing:0, partials:1, finals:0, empty:0, errors:0, other:0, commits:0
       }}));
     }
     if (message.type === 'finish_utterance') {
       assert.ok(voiceFrames > 0);
       if (++finishRequests === 1) {
         ws.send(JSON.stringify({type:'prisma_notice', code:'utterance_error', message:'private upstream response'}));
       } else {
         ws.send(JSON.stringify({type:'transcript', text:'Synthetic microphone test', final:true, sequence:1}));
       }
     }
   });
 });
 const capturePage = await capture.newPage();
 await capturePage.goto(baseURL + '/#live');
 await capturePage.getByRole('button',{name:'Start voice visit'}).click();
 for (let attempt = 0; voiceFrames === 0 && attempt < 100; attempt++) {
   await new Promise(resolve => setTimeout(resolve, 50));
 }
 assert.ok(voiceFrames > 0);
 await capturePage.locator('#partial-transcript').getByText('Partial · Synthetic partial text').waitFor();
 await capturePage.locator('#voice-diagnostics summary').click();
 await capturePage.locator('#voice-prisma-events').getByText(/1 partial/).waitFor();
 assert.ok(!(await capturePage.locator('#voice-diagnostics').innerText()).includes('Synthetic partial text'));
 await capturePage.getByRole('button',{name:'Finish speaking'}).click();
 await capturePage.locator('#prisma-issue').getByText(/couldn’t transcribe/).waitFor();
 assert.ok(!(await capturePage.locator('#voice-diagnostics').innerText()).includes('private upstream response'));
 await capturePage.getByRole('button',{name:'Finish speaking'}).click();
 await capturePage.locator('#transcript').getByText('Synthetic microphone test').waitFor({timeout:10000});
 await capturePage.locator('#partial-transcript').waitFor({state:'hidden'});
 await capturePage.locator('#prisma-issue').waitFor({state:'hidden'});
 await capturePage.getByRole('button',{name:'Stop session'}).click();
 assert.equal(displayedSequence, 1);
 await capturePage.locator('#transcript').getByText('Synthetic microphone test').waitFor();
 await capturePage.locator('#voice-transcript-delivery').getByText('Nonempty final text displayed').waitFor();
 assert.ok(!(await capturePage.locator('#voice-diagnostics').innerText()).includes('Synthetic microphone test'));
 await capturePage.getByRole('button',{name:'Resume voice visit'}).click();
 await capturePage.locator('#transcript').getByText('Synthetic microphone test').waitFor();
 await capture.close();
 console.log('Browser smoke passed: keyboard entry, mobile width, Hindi, danger, correction, offline draft, reconnect, confirm, history, follow-up, simulated permission denial, synthetic microphone PCM over WebSocket.');
 } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
