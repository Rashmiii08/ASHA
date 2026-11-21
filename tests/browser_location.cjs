const assert = require("node:assert/strict");
const {signIn} = require("./browser_auth_support.cjs");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const url = process.env.FIELD_REGISTER_GPS_URL || "https://localhost:8767";
const tile = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j9L8AAAAASUVORK5CYII=", "base64");

async function enterVisit(page) {
  await page.goto(`${url}/#live`);
  await page.getByRole("button", {name: "Start voice visit"}).click();
  await page.locator("#assistant").getByText(/AI assistant/).waitFor();
}

async function enterDetails(page, id) {
  await page.locator("#demo-text").fill(`Beneficiary ID ${id}. General health visit.`);
  await page.getByRole("button", {name: "Send transcript"}).click();
  await page.locator("#details").getByText(id, {exact: true}).waitFor();
}

async function save(page) {
  await page.getByRole("button", {name: "Stop session"}).click();
  await page.getByRole("button", {name: "Save Visit"}).click();
  await page.getByText(/Visit saved successfully/).first().waitFor();
}

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROMIUM_EXECUTABLE});
  try {
    const context = await browser.newContext({
      ignoreHTTPSErrors: true, permissions: ["geolocation"],
      geolocation: {latitude: 12.345678, longitude: 76.54321, accuracy: 12},
    });
    await signIn(context, url);
    let tileRequests = 0;
    await context.route("https://tile.openstreetmap.org/**", route => {
      tileRequests += 1;
      return route.fulfill({body: tile, contentType: "image/png"});
    });
    await context.addInitScript(() => {
      const original = navigator.geolocation.getCurrentPosition.bind(navigator.geolocation);
      window.locationRequests = [];
      navigator.geolocation.getCurrentPosition = (success, error, options) => {
        window.locationRequests.push(options);
        original(success, error, options);
      };
    });
    const page = await context.newPage();
    await page.goto(`${url}/#live`);
    assert.equal(await page.evaluate(() => window.locationRequests.length), 0);
    await enterVisit(page);
    await page.getByText("GPS location attached to this visit.").waitFor();
    assert.deepEqual(await page.evaluate(() => window.locationRequests), [{enableHighAccuracy:true, timeout:10000, maximumAge:0}]);
    assert.equal(tileRequests, 0);
    assert.equal(await page.locator("#location-accuracy").textContent(), "±12 m");
    await page.getByRole("button", {name: "Show map"}).click();
    await page.locator(".leaflet-marker-icon").waitFor();
    await page.getByRole("button", {name: "Zoom in"}).click();
    assert.ok(tileRequests > 0);
    assert.ok(!(await page.evaluate(() => JSON.stringify(Object.values(sessionStorage)))).includes("12.345678"));
    await context.setGeolocation({latitude: 13.234567, longitude: 77.54321, accuracy: 9});
    await page.getByRole("button", {name: "Refresh Location"}).click();
    await page.locator("#location-latitude").getByText("13.234567").waitFor();
    await page.getByText("GPS location attached to this visit.").waitFor();
    await page.reload();
    await page.getByRole("button", {name: "Start voice visit"}).click();
    await page.locator("#location-latitude").getByText("13.234567").waitFor();
    assert.equal(await page.evaluate(() => window.locationRequests.length), 0);
    await enterDetails(page, "1234");
    await save(page);
    assert.ok(await page.getByRole("button", {name: "Refresh Location"}).isDisabled());
    await page.getByRole("link", {name: "Visit History"}).click();
    const item = page.locator("#records article").filter({hasText:"1234"});
    const savedMap = await item.getByRole("link", {name:"Open saved location map"}).getAttribute("href");
    assert.ok(savedMap.includes("mlat=13.234567"));
    await page.reload();
    await item.getByRole("link", {name:"Open saved location map"}).waitFor();
    await context.close();

    for (const [code, message, id] of [
      [1, /Location permission was denied/, "1235"],
      [2, /GPS location is unavailable/, "1236"],
      [3, /GPS capture timed out/, "1237"],
    ]) {
      const unavailable = await browser.newContext({ignoreHTTPSErrors:true});
      await signIn(unavailable, url);
      await unavailable.addInitScript(code => {
        navigator.geolocation.getCurrentPosition = (_success, error) => error({code});
      }, code);
      const page = await unavailable.newPage();
      await enterVisit(page);
      await page.locator("#location-status").getByText(message).waitFor();
      await enterDetails(page, id);
      await save(page);
      await page.getByText("This visit was saved without location.").waitFor();
      await unavailable.close();
    }
    const unanswered = await browser.newContext({ignoreHTTPSErrors:true});
    await signIn(unanswered, url);
    await unanswered.addInitScript(() => { navigator.geolocation.getCurrentPosition = () => {}; });
    const waiting = await unanswered.newPage();
    await enterVisit(waiting);
    await waiting.getByRole("button", {name:"Continue without location"}).click();
    await enterDetails(waiting, "1238");
    await save(waiting);
    await waiting.getByText("This visit was saved without location.").waitFor();
    await unanswered.close();
    console.log("GPS browser checks passed: user-initiated capture, map, refresh, recovery, history, denied, unavailable, timeout.");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
