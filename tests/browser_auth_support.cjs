const assert = require("node:assert/strict");

async function signIn(context, url) {
  const password = process.env.ASHA_TEST_PASSWORD;
  assert.ok(password, "Set an ephemeral synthetic browser test password.");
  const response = await context.request.post(new URL("/api/auth/login", url).href, {
    headers: {Origin: new URL(url).origin}, data: {worker_id: "BROWSER-WORKER", password},
  });
  assert.equal(response.status(), 200);
}

module.exports = {signIn};
