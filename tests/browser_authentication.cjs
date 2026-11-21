const assert = require("node:assert/strict");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const url = process.env.FIELD_REGISTER_AUTH_URL || "https://localhost:8768";
const origin = new URL(url).origin;
const password = process.env.ASHA_TEST_PASSWORD;

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROMIUM_EXECUTABLE});
  try {
    const workerContext = await browser.newContext({ignoreHTTPSErrors:true, viewport:{width:390,height:844}});
    const workerPage = await workerContext.newPage();
    await workerPage.goto(url + "/#history");
    await workerPage.locator("#account").getByRole("heading", {name:"Sign in"}).waitFor();
    assert.ok(await workerPage.locator("#history").isHidden());
    await workerPage.getByRole("button", {name:"Register as a worker"}).click();
    await workerPage.getByLabel("Full name").fill("Synthetic Applicant");
    await workerPage.getByLabel("Mobile number").fill("987654321");
    await workerPage.getByLabel("Assigned village or area").fill("Requested area");
    await workerPage.getByLabel("Worker ID").fill("APPLICANT");
    await workerPage.getByLabel("Password", {exact:true}).fill(password);
    await workerPage.getByLabel("Confirm password", {exact:true}).fill(password);
    await workerPage.locator("#account-submit").click();
    await workerPage.getByText(/10-digit Indian mobile number/).waitFor();
    assert.equal(await workerPage.getByLabel("Password", {exact:true}).inputValue(), password);
    await workerPage.getByLabel("Mobile number").fill("9876543210");
    await workerPage.locator("#account-submit").click();
    await workerPage.getByText(/administrator must approve/).waitFor();

    const adminContext = await browser.newContext({ignoreHTTPSErrors:true});
    const adminLogin = await adminContext.request.post(origin + "/api/auth/login", {
      headers: {Origin:origin}, data:{worker_id:"BROWSER-ADMIN", password},
    });
    assert.equal(adminLogin.status(), 200);
    const admin = await adminContext.newPage();
    await admin.goto(url);
    await admin.getByRole("heading", {name:"Worker administration"}).waitFor();
    await admin.getByLabel("Area for APPLICANT").fill("Approved area");
    await admin.getByLabel("Status for APPLICANT").selectOption("active");
    await admin.locator(".account-row").filter({hasText:"APPLICANT"}).getByRole("button", {name:"Save worker assignment"}).click();
    await admin.getByText("Worker updated.").waitFor();

    await workerPage.getByRole("button", {name:"Sign in", exact:true}).click();
    await workerPage.getByLabel("Worker ID").fill("APPLICANT");
    await workerPage.getByLabel("Password", {exact:true}).fill(password);
    await workerPage.locator("#account-submit").click();
    await workerPage.getByRole("heading", {name:"Visit history"}).waitFor();
    await workerPage.getByRole("link", {name:"Dashboard"}).click();
    await workerPage.getByRole("heading", {name:"Welcome, Synthetic Applicant"}).waitFor();
    await workerPage.getByText("Worker ID APPLICANT · Approved area").waitFor();
    assert.ok(!(await workerPage.evaluate(() => JSON.stringify(localStorage))).includes(password));
    await workerPage.getByRole("button", {name:"Sign out"}).click();
    await workerPage.getByText("Signed out.").waitFor();
    assert.ok(await workerPage.locator("#dashboard").isHidden());
    console.log("Authentication browser checks passed: registration, administrator approval, mobile dashboard, logout.");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
